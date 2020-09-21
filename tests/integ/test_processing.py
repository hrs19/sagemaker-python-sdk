# Copyright 2019-2020 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You
# may not use this file except in compliance with the License. A copy of
# the License is located at
#
#     http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is
# distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.
from __future__ import absolute_import

import os

import pytest
from botocore.config import Config

from sagemaker import image_uris, Session
from sagemaker.network import NetworkConfig
from sagemaker.processing import (
    ProcessingInput,
    ProcessingOutput,
    ScriptProcessor,
    Processor,
    ProcessingJob,
)
from sagemaker.sklearn.processing import SKLearnProcessor
from tests.integ import DATA_DIR
from tests.integ.kms_utils import get_or_create_kms_key

ROLE = "SageMakerRole"


@pytest.fixture(scope="module")
def sagemaker_session_with_custom_bucket(
    boto_session, sagemaker_client_config, sagemaker_runtime_config, custom_bucket_name
):
    sagemaker_client_config.setdefault("config", Config(retries=dict(max_attempts=10)))
    sagemaker_client = (
        boto_session.client("sagemaker", **sagemaker_client_config)
        if sagemaker_client_config
        else None
    )
    runtime_client = (
        boto_session.client("sagemaker-runtime", **sagemaker_runtime_config)
        if sagemaker_runtime_config
        else None
    )

    return Session(
        boto_session=boto_session,
        sagemaker_client=sagemaker_client,
        sagemaker_runtime_client=runtime_client,
        default_bucket=custom_bucket_name,
    )


@pytest.fixture(scope="module")
def image_uri(
    sklearn_latest_version,
    sklearn_latest_py_version,
    cpu_instance_type,
    sagemaker_session,
):
    return image_uris.retrieve(
        "sklearn",
        sagemaker_session.boto_region_name,
        version=sklearn_latest_version,
        py_version=sklearn_latest_py_version,
        instance_type=cpu_instance_type,
    )


@pytest.fixture(scope="module")
def volume_kms_key(sagemaker_session):
    role_arn = sagemaker_session.expand_role(ROLE)
    return get_or_create_kms_key(
        sagemaker_session=sagemaker_session,
        role_arn=role_arn,
        alias="integ-test-processing-volume-kms-key-{}".format(
            sagemaker_session.boto_session.region_name
        ),
    )


@pytest.fixture(scope="module")
def input_kms_key(sagemaker_session):
    role_arn = sagemaker_session.expand_role(ROLE)
    return get_or_create_kms_key(
        sagemaker_session=sagemaker_session,
        role_arn=role_arn,
        alias="integ-test-processing-input-kms-key-{}".format(
            sagemaker_session.boto_session.region_name
        ),
    )


@pytest.fixture(scope="module")
def output_kms_key(sagemaker_session):
    role_arn = sagemaker_session.expand_role(ROLE)
    return get_or_create_kms_key(
        sagemaker_session=sagemaker_session,
        role_arn=role_arn,
        alias="integ-test-processing-output-kms-key-{}".format(
            sagemaker_session.boto_session.region_name
        ),
    )


def test_sklearn(sagemaker_session, sklearn_latest_version, cpu_instance_type):
    script_path = os.path.join(DATA_DIR, "dummy_script.py")
    input_file_path = os.path.join(DATA_DIR, "dummy_input.txt")

    sklearn_processor = SKLearnProcessor(
        framework_version=sklearn_latest_version,
        role=ROLE,
        instance_type=cpu_instance_type,
        instance_count=1,
        command=["python3"],
        sagemaker_session=sagemaker_session,
        base_job_name="test-sklearn",
    )

    sklearn_processor.run(
        code=script_path,
        inputs=[ProcessingInput(source=input_file_path, destination="/opt/ml/processing/inputs/")],
        wait=False,
        logs=False,
    )

    job_description = sklearn_processor.latest_job.describe()

    assert len(job_description["ProcessingInputs"]) == 2
    assert job_description["ProcessingResources"]["ClusterConfig"]["InstanceCount"] == 1
    assert (
        job_description["ProcessingResources"]["ClusterConfig"]["InstanceType"] == cpu_instance_type
    )
    assert job_description["ProcessingResources"]["ClusterConfig"]["VolumeSizeInGB"] == 30
    assert job_description["StoppingCondition"] == {"MaxRuntimeInSeconds": 86400}
    assert job_description["AppSpecification"]["ContainerEntrypoint"] == [
        "python3",
        "/opt/ml/processing/input/code/dummy_script.py",
    ]
    assert ROLE in job_description["RoleArn"]


@pytest.mark.canary_quick
def test_sklearn_with_customizations(
    sagemaker_session, image_uri, sklearn_latest_version, cpu_instance_type, output_kms_key
):
    input_file_path = os.path.join(DATA_DIR, "dummy_input.txt")

    sklearn_processor = SKLearnProcessor(
        framework_version=sklearn_latest_version,
        role=ROLE,
        command=["python3"],
        instance_type=cpu_instance_type,
        instance_count=1,
        volume_size_in_gb=100,
        volume_kms_key=None,
        output_kms_key=output_kms_key,
        max_runtime_in_seconds=3600,
        base_job_name="test-sklearn-with-customizations",
        env={"DUMMY_ENVIRONMENT_VARIABLE": "dummy-value"},
        tags=[{"Key": "dummy-tag", "Value": "dummy-tag-value"}],
        sagemaker_session=sagemaker_session,
    )

    sklearn_processor.run(
        code=os.path.join(DATA_DIR, "dummy_script.py"),
        inputs=[
            ProcessingInput(
                source=input_file_path,
                destination="/opt/ml/processing/input/container/path/",
                input_name="dummy_input",
                s3_data_type="S3Prefix",
                s3_input_mode="File",
                s3_data_distribution_type="FullyReplicated",
                s3_compression_type="None",
            )
        ],
        outputs=[
            ProcessingOutput(
                source="/opt/ml/processing/output/container/path/",
                output_name="dummy_output",
                s3_upload_mode="EndOfJob",
            )
        ],
        arguments=["-v"],
        wait=True,
        logs=True,
    )

    job_description = sklearn_processor.latest_job.describe()

    assert job_description["ProcessingInputs"][0]["InputName"] == "dummy_input"

    assert job_description["ProcessingInputs"][1]["InputName"] == "code"

    assert job_description["ProcessingJobName"].startswith("test-sklearn-with-customizations")

    assert job_description["ProcessingJobStatus"] == "Completed"

    assert job_description["ProcessingOutputConfig"]["KmsKeyId"] == output_kms_key
    assert job_description["ProcessingOutputConfig"]["Outputs"][0]["OutputName"] == "dummy_output"

    assert job_description["ProcessingResources"]["ClusterConfig"]["InstanceCount"] == 1
    assert (
        job_description["ProcessingResources"]["ClusterConfig"]["InstanceType"] == cpu_instance_type
    )
    assert job_description["ProcessingResources"]["ClusterConfig"]["VolumeSizeInGB"] == 100

    assert job_description["AppSpecification"]["ContainerArguments"] == ["-v"]
    assert job_description["AppSpecification"]["ContainerEntrypoint"] == [
        "python3",
        "/opt/ml/processing/input/code/dummy_script.py",
    ]
    assert job_description["AppSpecification"]["ImageUri"] == image_uri

    assert job_description["Environment"] == {"DUMMY_ENVIRONMENT_VARIABLE": "dummy-value"}

    assert ROLE in job_description["RoleArn"]

    assert job_description["StoppingCondition"] == {"MaxRuntimeInSeconds": 3600}


def test_sklearn_with_custom_default_bucket(
    sagemaker_session_with_custom_bucket,
    custom_bucket_name,
    image_uri,
    sklearn_latest_version,
    cpu_instance_type,
    output_kms_key,
):
    input_file_path = os.path.join(DATA_DIR, "dummy_input.txt")

    sklearn_processor = SKLearnProcessor(
        framework_version=sklearn_latest_version,
        role=ROLE,
        command=["python3"],
        instance_type=cpu_instance_type,
        instance_count=1,
        volume_size_in_gb=100,
        volume_kms_key=None,
        output_kms_key=output_kms_key,
        max_runtime_in_seconds=3600,
        base_job_name="test-sklearn-with-customizations",
        env={"DUMMY_ENVIRONMENT_VARIABLE": "dummy-value"},
        tags=[{"Key": "dummy-tag", "Value": "dummy-tag-value"}],
        sagemaker_session=sagemaker_session_with_custom_bucket,
    )

    sklearn_processor.run(
        code=os.path.join(DATA_DIR, "dummy_script.py"),
        inputs=[
            ProcessingInput(
                source=input_file_path,
                destination="/opt/ml/processing/input/container/path/",
                input_name="dummy_input",
                s3_data_type="S3Prefix",
                s3_input_mode="File",
                s3_data_distribution_type="FullyReplicated",
                s3_compression_type="None",
            )
        ],
        outputs=[
            ProcessingOutput(
                source="/opt/ml/processing/output/container/path/",
                output_name="dummy_output",
                s3_upload_mode="EndOfJob",
            )
        ],
        arguments=["-v"],
        wait=True,
        logs=True,
    )

    job_description = sklearn_processor.latest_job.describe()

    assert job_description["ProcessingInputs"][0]["InputName"] == "dummy_input"
    assert custom_bucket_name in job_description["ProcessingInputs"][0]["S3Input"]["S3Uri"]

    assert job_description["ProcessingInputs"][1]["InputName"] == "code"
    assert custom_bucket_name in job_description["ProcessingInputs"][1]["S3Input"]["S3Uri"]

    assert job_description["ProcessingJobName"].startswith("test-sklearn-with-customizations")

    assert job_description["ProcessingJobStatus"] == "Completed"

    assert job_description["ProcessingOutputConfig"]["KmsKeyId"] == output_kms_key
    assert job_description["ProcessingOutputConfig"]["Outputs"][0]["OutputName"] == "dummy_output"

    assert job_description["ProcessingResources"]["ClusterConfig"]["InstanceCount"] == 1
    assert (
        job_description["ProcessingResources"]["ClusterConfig"]["InstanceType"] == cpu_instance_type
    )
    assert job_description["ProcessingResources"]["ClusterConfig"]["VolumeSizeInGB"] == 100

    assert job_description["AppSpecification"]["ContainerArguments"] == ["-v"]
    assert job_description["AppSpecification"]["ContainerEntrypoint"] == [
        "python3",
        "/opt/ml/processing/input/code/dummy_script.py",
    ]
    assert job_description["AppSpecification"]["ImageUri"] == image_uri

    assert job_description["Environment"] == {"DUMMY_ENVIRONMENT_VARIABLE": "dummy-value"}

    assert ROLE in job_description["RoleArn"]

    assert job_description["StoppingCondition"] == {"MaxRuntimeInSeconds": 3600}


def test_sklearn_with_no_inputs_or_outputs(
    sagemaker_session, image_uri, sklearn_latest_version, cpu_instance_type
):
    sklearn_processor = SKLearnProcessor(
        framework_version=sklearn_latest_version,
        role=ROLE,
        command=["python3"],
        instance_type=cpu_instance_type,
        instance_count=1,
        volume_size_in_gb=100,
        volume_kms_key=None,
        max_runtime_in_seconds=3600,
        base_job_name="test-sklearn-with-no-inputs-or-outputs",
        env={"DUMMY_ENVIRONMENT_VARIABLE": "dummy-value"},
        tags=[{"Key": "dummy-tag", "Value": "dummy-tag-value"}],
        sagemaker_session=sagemaker_session,
    )

    sklearn_processor.run(
        code=os.path.join(DATA_DIR, "dummy_script.py"), arguments=["-v"], wait=True, logs=True
    )

    job_description = sklearn_processor.latest_job.describe()

    assert job_description["ProcessingInputs"][0]["InputName"] == "code"

    assert job_description["ProcessingJobName"].startswith("test-sklearn-with-no-inputs")

    assert job_description["ProcessingJobStatus"] == "Completed"

    assert job_description["ProcessingResources"]["ClusterConfig"]["InstanceCount"] == 1
    assert (
        job_description["ProcessingResources"]["ClusterConfig"]["InstanceType"] == cpu_instance_type
    )
    assert job_description["ProcessingResources"]["ClusterConfig"]["VolumeSizeInGB"] == 100

    assert job_description["AppSpecification"]["ContainerArguments"] == ["-v"]
    assert job_description["AppSpecification"]["ContainerEntrypoint"] == [
        "python3",
        "/opt/ml/processing/input/code/dummy_script.py",
    ]
    assert job_description["AppSpecification"]["ImageUri"] == image_uri

    assert job_description["Environment"] == {"DUMMY_ENVIRONMENT_VARIABLE": "dummy-value"}

    assert ROLE in job_description["RoleArn"]

    assert job_description["StoppingCondition"] == {"MaxRuntimeInSeconds": 3600}


@pytest.mark.canary_quick
def test_script_processor(sagemaker_session, image_uri, cpu_instance_type, output_kms_key):
    input_file_path = os.path.join(DATA_DIR, "dummy_input.txt")

    script_processor = ScriptProcessor(
        role=ROLE,
        image_uri=image_uri,
        command=["python3"],
        instance_count=1,
        instance_type=cpu_instance_type,
        volume_size_in_gb=100,
        volume_kms_key=None,
        output_kms_key=output_kms_key,
        max_runtime_in_seconds=3600,
        base_job_name="test-script-processor",
        env={"DUMMY_ENVIRONMENT_VARIABLE": "dummy-value"},
        tags=[{"Key": "dummy-tag", "Value": "dummy-tag-value"}],
        sagemaker_session=sagemaker_session,
    )

    script_processor.run(
        code=os.path.join(DATA_DIR, "dummy_script.py"),
        inputs=[
            ProcessingInput(
                source=input_file_path,
                destination="/opt/ml/processing/input/container/path/",
                input_name="dummy_input",
                s3_data_type="S3Prefix",
                s3_input_mode="File",
                s3_data_distribution_type="FullyReplicated",
                s3_compression_type="None",
            )
        ],
        outputs=[
            ProcessingOutput(
                source="/opt/ml/processing/output/container/path/",
                output_name="dummy_output",
                s3_upload_mode="EndOfJob",
            )
        ],
        arguments=["-v"],
        wait=True,
        logs=True,
    )

    job_description = script_processor.latest_job.describe()

    assert job_description["ProcessingInputs"][0]["InputName"] == "dummy_input"

    assert job_description["ProcessingInputs"][1]["InputName"] == "code"

    assert job_description["ProcessingJobName"].startswith("test-script-processor")

    assert job_description["ProcessingJobStatus"] == "Completed"

    assert job_description["ProcessingOutputConfig"]["KmsKeyId"] == output_kms_key
    assert job_description["ProcessingOutputConfig"]["Outputs"][0]["OutputName"] == "dummy_output"

    assert job_description["ProcessingResources"]["ClusterConfig"]["InstanceCount"] == 1
    assert (
        job_description["ProcessingResources"]["ClusterConfig"]["InstanceType"] == cpu_instance_type
    )
    assert job_description["ProcessingResources"]["ClusterConfig"]["VolumeSizeInGB"] == 100

    assert job_description["AppSpecification"]["ContainerArguments"] == ["-v"]
    assert job_description["AppSpecification"]["ContainerEntrypoint"] == [
        "python3",
        "/opt/ml/processing/input/code/dummy_script.py",
    ]
    assert job_description["AppSpecification"]["ImageUri"] == image_uri

    assert job_description["Environment"] == {"DUMMY_ENVIRONMENT_VARIABLE": "dummy-value"}

    assert ROLE in job_description["RoleArn"]

    assert job_description["StoppingCondition"] == {"MaxRuntimeInSeconds": 3600}


def test_script_processor_with_no_inputs_or_outputs(
    sagemaker_session, image_uri, cpu_instance_type
):
    script_processor = ScriptProcessor(
        role=ROLE,
        image_uri=image_uri,
        command=["python3"],
        instance_count=1,
        instance_type=cpu_instance_type,
        volume_size_in_gb=100,
        volume_kms_key=None,
        max_runtime_in_seconds=3600,
        base_job_name="test-script-processor-with-no-inputs-or-outputs",
        env={"DUMMY_ENVIRONMENT_VARIABLE": "dummy-value"},
        tags=[{"Key": "dummy-tag", "Value": "dummy-tag-value"}],
        sagemaker_session=sagemaker_session,
    )

    script_processor.run(
        code=os.path.join(DATA_DIR, "dummy_script.py"), arguments=["-v"], wait=True, logs=True
    )

    job_description = script_processor.latest_job.describe()

    assert job_description["ProcessingInputs"][0]["InputName"] == "code"

    assert job_description["ProcessingJobName"].startswith("test-script-processor-with-no-inputs")

    assert job_description["ProcessingJobStatus"] == "Completed"

    assert job_description["ProcessingResources"]["ClusterConfig"]["InstanceCount"] == 1
    assert (
        job_description["ProcessingResources"]["ClusterConfig"]["InstanceType"] == cpu_instance_type
    )
    assert job_description["ProcessingResources"]["ClusterConfig"]["VolumeSizeInGB"] == 100

    assert job_description["AppSpecification"]["ContainerArguments"] == ["-v"]
    assert job_description["AppSpecification"]["ContainerEntrypoint"] == [
        "python3",
        "/opt/ml/processing/input/code/dummy_script.py",
    ]
    assert job_description["AppSpecification"]["ImageUri"] == image_uri

    assert job_description["Environment"] == {"DUMMY_ENVIRONMENT_VARIABLE": "dummy-value"}

    assert ROLE in job_description["RoleArn"]

    assert job_description["StoppingCondition"] == {"MaxRuntimeInSeconds": 3600}

    job_from_name = ProcessingJob.from_processing_name(
        sagemaker_session=sagemaker_session,
        processing_job_name=job_description["ProcessingJobName"],
    )
    job_description = job_from_name.describe()

    assert job_description["ProcessingInputs"][0]["InputName"] == "code"

    assert job_description["ProcessingJobName"].startswith("test-script-processor-with-no-inputs")

    assert job_description["ProcessingJobStatus"] == "Completed"

    assert job_description["ProcessingResources"]["ClusterConfig"]["InstanceCount"] == 1
    assert (
        job_description["ProcessingResources"]["ClusterConfig"]["InstanceType"] == cpu_instance_type
    )
    assert job_description["ProcessingResources"]["ClusterConfig"]["VolumeSizeInGB"] == 100

    assert job_description["AppSpecification"]["ContainerArguments"] == ["-v"]
    assert job_description["AppSpecification"]["ContainerEntrypoint"] == [
        "python3",
        "/opt/ml/processing/input/code/dummy_script.py",
    ]
    assert job_description["AppSpecification"]["ImageUri"] == image_uri

    assert job_description["Environment"] == {"DUMMY_ENVIRONMENT_VARIABLE": "dummy-value"}

    assert ROLE in job_description["RoleArn"]

    assert job_description["StoppingCondition"] == {"MaxRuntimeInSeconds": 3600}


@pytest.mark.canary_quick
def test_processor(sagemaker_session, image_uri, cpu_instance_type, output_kms_key):
    script_path = os.path.join(DATA_DIR, "dummy_script.py")

    processor = Processor(
        role=ROLE,
        image_uri=image_uri,
        instance_count=1,
        instance_type=cpu_instance_type,
        entrypoint=["python3", "/opt/ml/processing/input/code/dummy_script.py"],
        volume_size_in_gb=100,
        volume_kms_key=None,
        output_kms_key=output_kms_key,
        max_runtime_in_seconds=3600,
        base_job_name="test-processor",
        env={"DUMMY_ENVIRONMENT_VARIABLE": "dummy-value"},
        tags=[{"Key": "dummy-tag", "Value": "dummy-tag-value"}],
        sagemaker_session=sagemaker_session,
    )

    processor.run(
        inputs=[
            ProcessingInput(
                source=script_path, destination="/opt/ml/processing/input/code/", input_name="code"
            )
        ],
        outputs=[
            ProcessingOutput(
                source="/opt/ml/processing/output/container/path/",
                output_name="dummy_output",
                s3_upload_mode="EndOfJob",
            )
        ],
        arguments=["-v"],
        wait=True,
        logs=True,
    )

    job_description = processor.latest_job.describe()

    assert job_description["ProcessingInputs"][0]["InputName"] == "code"

    assert job_description["ProcessingJobName"].startswith("test-processor")

    assert job_description["ProcessingJobStatus"] == "Completed"

    assert job_description["ProcessingOutputConfig"]["KmsKeyId"] == output_kms_key
    assert job_description["ProcessingOutputConfig"]["Outputs"][0]["OutputName"] == "dummy_output"

    assert job_description["ProcessingResources"]["ClusterConfig"]["InstanceCount"] == 1
    assert (
        job_description["ProcessingResources"]["ClusterConfig"]["InstanceType"] == cpu_instance_type
    )
    assert job_description["ProcessingResources"]["ClusterConfig"]["VolumeSizeInGB"] == 100

    assert job_description["AppSpecification"]["ContainerArguments"] == ["-v"]
    assert job_description["AppSpecification"]["ContainerEntrypoint"] == [
        "python3",
        "/opt/ml/processing/input/code/dummy_script.py",
    ]
    assert job_description["AppSpecification"]["ImageUri"] == image_uri

    assert job_description["Environment"] == {"DUMMY_ENVIRONMENT_VARIABLE": "dummy-value"}

    assert ROLE in job_description["RoleArn"]

    assert job_description["StoppingCondition"] == {"MaxRuntimeInSeconds": 3600}


def test_processor_with_custom_bucket(
    sagemaker_session_with_custom_bucket,
    custom_bucket_name,
    image_uri,
    cpu_instance_type,
    output_kms_key,
    input_kms_key,
):
    script_path = os.path.join(DATA_DIR, "dummy_script.py")

    processor = Processor(
        role=ROLE,
        image_uri=image_uri,
        instance_count=1,
        instance_type=cpu_instance_type,
        entrypoint=["python3", "/opt/ml/processing/input/code/dummy_script.py"],
        volume_size_in_gb=100,
        volume_kms_key=None,
        output_kms_key=output_kms_key,
        max_runtime_in_seconds=3600,
        base_job_name="test-processor",
        env={"DUMMY_ENVIRONMENT_VARIABLE": "dummy-value"},
        tags=[{"Key": "dummy-tag", "Value": "dummy-tag-value"}],
        sagemaker_session=sagemaker_session_with_custom_bucket,
    )

    processor.run(
        inputs=[
            ProcessingInput(
                source=script_path, destination="/opt/ml/processing/input/code/", input_name="code"
            )
        ],
        kms_key=input_kms_key,
        outputs=[
            ProcessingOutput(
                source="/opt/ml/processing/output/container/path/",
                output_name="dummy_output",
                s3_upload_mode="EndOfJob",
            )
        ],
        arguments=["-v"],
        wait=True,
        logs=True,
    )

    job_description = processor.latest_job.describe()

    assert job_description["ProcessingInputs"][0]["InputName"] == "code"
    assert custom_bucket_name in job_description["ProcessingInputs"][0]["S3Input"]["S3Uri"]

    assert job_description["ProcessingJobName"].startswith("test-processor")

    assert job_description["ProcessingJobStatus"] == "Completed"

    assert job_description["ProcessingOutputConfig"]["KmsKeyId"] == output_kms_key
    assert job_description["ProcessingOutputConfig"]["Outputs"][0]["OutputName"] == "dummy_output"

    assert job_description["ProcessingResources"]["ClusterConfig"]["InstanceCount"] == 1
    assert (
        job_description["ProcessingResources"]["ClusterConfig"]["InstanceType"] == cpu_instance_type
    )
    assert job_description["ProcessingResources"]["ClusterConfig"]["VolumeSizeInGB"] == 100

    assert job_description["AppSpecification"]["ContainerArguments"] == ["-v"]
    assert job_description["AppSpecification"]["ContainerEntrypoint"] == [
        "python3",
        "/opt/ml/processing/input/code/dummy_script.py",
    ]
    assert job_description["AppSpecification"]["ImageUri"] == image_uri

    assert job_description["Environment"] == {"DUMMY_ENVIRONMENT_VARIABLE": "dummy-value"}

    assert ROLE in job_description["RoleArn"]

    assert job_description["StoppingCondition"] == {"MaxRuntimeInSeconds": 3600}


def test_sklearn_with_network_config(sagemaker_session, sklearn_latest_version, cpu_instance_type):
    script_path = os.path.join(DATA_DIR, "dummy_script.py")
    input_file_path = os.path.join(DATA_DIR, "dummy_input.txt")

    sklearn_processor = SKLearnProcessor(
        framework_version=sklearn_latest_version,
        role=ROLE,
        instance_type=cpu_instance_type,
        instance_count=1,
        command=["python3"],
        sagemaker_session=sagemaker_session,
        base_job_name="test-sklearn-with-network-config",
        network_config=NetworkConfig(
            enable_network_isolation=True, encrypt_inter_container_traffic=True
        ),
    )

    sklearn_processor.run(
        code=script_path,
        inputs=[ProcessingInput(source=input_file_path, destination="/opt/ml/processing/inputs/")],
        wait=False,
        logs=False,
    )

    job_description = sklearn_processor.latest_job.describe()
    network_config = job_description["NetworkConfig"]
    assert network_config["EnableInterContainerTrafficEncryption"]
    assert network_config["EnableNetworkIsolation"]

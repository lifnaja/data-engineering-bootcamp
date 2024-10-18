import csv
import json

from airflow import DAG
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import BranchPythonOperator, PythonOperator
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from airflow.utils import timezone

import requests
from google.cloud import bigquery, storage
from google.oauth2 import service_account


BUCKET_NAME = "deb-bootcamp-YOUR_STUDENT_ID"
BUSINESS_DOMAIN = "greenery"
LOCATION = "asia-southeast1"
GCP_PROJECT_ID = "ํYOUR_GCP_PROJECT_ID"
DAGS_FOLDER = "/opt/airflow/dags"
DATA = "users"

        
def _extract_data(ds):
    url = f"http://34.87.139.82:8000/{DATA}/?created_at={ds}"
    response = requests.get(url)
    data = response.json()

    if data:
        with open(f"{DAGS_FOLDER}/{DATA}-{ds}.csv", "w") as f:
            writer = csv.writer(f)
            header = [
                "user_id",
                "first_name",
                "last_name",
                "email",
                "phone_number",
                "created_at",
                "updated_at",
                "address",
            ]
            writer.writerow(header)
            for each in data:
                data = [
                    each["user_id"],
                    each["first_name"],
                    each["last_name"],
                    each["email"],
                    each["phone_number"],
                    each["created_at"],
                    each["updated_at"],
                    each["address"]
                ]
                writer.writerow(data)
        return "load_data_to_gcs"
    else:
        return "do_nothing"


def _load_data_to_gcs(ds):
    keyfile_gcs = f"{DAGS_FOLDER}/YOUR_KEY_FILE_PATH"
    service_account_info_gcs = json.load(open(keyfile_gcs))
    credentials_gcs = service_account.Credentials.from_service_account_info(
        service_account_info_gcs
    )

    # Load data from Local to GCS
    storage_client = storage.Client(
        project=GCP_PROJECT_ID,
        credentials=credentials_gcs,
    )
    bucket = storage_client.bucket(BUCKET_NAME)

    file_path = f"{DAGS_FOLDER}/{DATA}-{ds}.csv"
    destination_blob_name = f"raw/{BUSINESS_DOMAIN}/{DATA}/{ds}/{DATA}.csv"
    blob = bucket.blob(destination_blob_name)
    blob.upload_from_filename(file_path)


def _load_data_from_gcs_to_bigquery(ds):
    "keyfile_bigquery = f"{DAGS_FOLDER}/YOUR_KEY_FILE_PATH""
    service_account_info_bigquery = json.load(open(keyfile_bigquery))
    credentials_bigquery = service_account.Credentials.from_service_account_info(
        service_account_info_bigquery
    )

    bigquery_client = bigquery.Client(
        project=GCP_PROJECT_ID,
        credentials=credentials_bigquery,
        location=LOCATION,
    )

    table_id = f"{GCP_PROJECT_ID}.deb_bootcamp.{DATA}"
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.PARQUET,
        time_partitioning=bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="created_at",
        ),
    )

    destination_blob_name = f"cleaned/{BUSINESS_DOMAIN}/{DATA}/{ds}/*.parquet"
    job = bigquery_client.load_table_from_uri(
        f"gs://{BUCKET_NAME}/{destination_blob_name}",
        table_id,
        job_config=job_config,
        location=LOCATION,
    )
    job.result()

    table = bigquery_client.get_table(table_id)
    print(f"Loaded {table.num_rows} rows and {len(table.schema)} columns to {table_id}")


default_args = {
    "owner": "airflow",
    "start_date": timezone.datetime(2020, 2, 9),
}
with DAG(
    dag_id="greenery_users_data_pipeline",
    default_args=default_args,
    schedule="@daily",
    catchup=False,
    tags=["DEB", "Skooldio", "greenery"],
):
    start = EmptyOperator(task_id="start")

    # Extract data from Postgres, API, or SFTP
    extract_data = BranchPythonOperator(
        task_id="extract_data",
        python_callable=_extract_data,
        op_kwargs={"ds": "{{ ds }}"},
    )

    do_nothing = EmptyOperator(task_id="do_nothing")

    # Load data to GCS
    load_data_to_gcs = PythonOperator(
        task_id="load_data_to_gcs",
        python_callable=_load_data_to_gcs,
        op_kwargs={"ds": "{{ ds }}"},
    )
    
    # Submit a Spark app to transform data
    transform_data = SparkSubmitOperator(
        task_id="transform_data",
        application="/opt/spark/pyspark/transform_users.py",
        conn_id="my_spark",
        env_vars={'EXECUTION_DATE': '{{ ds }}'}
    )

    # Load data from GCS to BigQuery
    load_data_from_gcs_to_bigquery = PythonOperator(
        task_id="load_data_from_gcs_to_bigquery",
        python_callable=_load_data_from_gcs_to_bigquery,
        op_kwargs={"ds": "{{ ds }}"},
    )

    end = EmptyOperator(task_id="end", trigger_rule="one_success")

    # Task dependencies
    start >> extract_data >> load_data_to_gcs >> transform_data >> load_data_from_gcs_to_bigquery >> end
    extract_data >> do_nothing >> end
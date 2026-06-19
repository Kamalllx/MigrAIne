terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.default_region
}

variable "project_id" {
  description = "GCP project id"
  type        = string
}

variable "default_region" {
  description = "Default GCP region"
  type        = string
  default     = "us-central1"
}

resource "google_cloud_run_v2_service" "cloudmart_api_gateway" {
  name     = "cloudmart-api-gateway"
  location = "us"
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  template {
    containers {
      image = "us-docker.pkg.dev/cloudrun/container/hello"
    }
  }
}

resource "google_cloud_run_v2_service" "cloudmart_user_service" {
  name     = "cloudmart-user-service"
  location = "us"
  ingress  = "INGRESS_TRAFFIC_ALL"
  template {
    containers {
      image = "us-docker.pkg.dev/cloudrun/container/hello"
    }
  }
}

resource "google_compute_disk" "instance_20260401_095527" {
  name  = "instance-20260401-095527"
  type  = "pd-balanced"
  zone  = "us-central1-b"
  size  = 50
  depends_on = [google_compute_instance.instance_20260401_095527_2]
}

resource "google_compute_instance" "instance_20260401_095527_2" {
  name         = "instance-20260401-095527"
  machine_type = "n2-standard-2"
  zone         = "us-central1-b"
  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-11"
    }
  }
  network_interface {
    subnetwork = "default"
    access_config {}
  }
  service_account {
    email  = "277990001367-compute@developer.gserviceaccount.com"
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }
}

resource "google_container_cluster" "cloudmart_cluster" {
  name     = "cloudmart-cluster"
  location = "us"
  remove_default_node_pool = true
  initial_node_count       = 1
}

resource "google_artifact_registry_repository" "projects_chessworld_gcp_locations_us_central1_re" {
  repository_id = "projects_chessworld_gcp_locations_us_central1_re"
  location      = "us"
  format        = "DOCKER"
}

resource "google_bigquery_dataset" "cloudmart_analytics" {
  dataset_id = "cloudmart_analytics"
  location   = "US"
}

resource "google_bigquery_table" "orders" {
  dataset_id = "REPLACE_ME_DATASET"
  table_id   = "orders"
  deletion_protection = false
  schema = jsonencode([{name = "id", type = "STRING", mode = "NULLABLE"}])
}

resource "google_bigquery_table" "pageviews" {
  dataset_id = "REPLACE_ME_DATASET"
  table_id   = "pageviews"
  deletion_protection = false
  schema = jsonencode([{name = "id", type = "STRING", mode = "NULLABLE"}])
}

resource "google_firestore_database" "projects_chessworld_gcp_databases_default" {
  name        = "(default)"
  location_id = "us-central"
  type        = "FIRESTORE_NATIVE"
}

resource "google_pubsub_subscription" "projects_chessworld_gcp_subscriptions_cloudmart_" {
  name  = "projects/chessworld-gcp/subscriptions/cloudmart-inventory-worker"
  topic = "REPLACE_ME_TOPIC"
}

resource "google_pubsub_subscription" "projects_chessworld_gcp_subscriptions_cloudmart__2" {
  name  = "projects/chessworld-gcp/subscriptions/cloudmart-orders-analytics"
  topic = "REPLACE_ME_TOPIC"
}

resource "google_pubsub_subscription" "projects_chessworld_gcp_subscriptions_cloudmart__3" {
  name  = "projects/chessworld-gcp/subscriptions/cloudmart-orders-notifier"
  topic = "REPLACE_ME_TOPIC"
}

resource "google_pubsub_topic" "projects_chessworld_gcp_topics_cloudmart_analyti" {
  name = "projects/chessworld-gcp/topics/cloudmart-analytics-trigger"
}

resource "google_pubsub_topic" "projects_chessworld_gcp_topics_cloudmart_dlq" {
  name = "projects/chessworld-gcp/topics/cloudmart-dlq"
}

resource "google_pubsub_topic" "projects_chessworld_gcp_topics_cloudmart_invento" {
  name = "projects/chessworld-gcp/topics/cloudmart-inventory"
}

resource "google_pubsub_topic" "projects_chessworld_gcp_topics_cloudmart_orders" {
  name = "projects/chessworld-gcp/topics/cloudmart-orders"
}

resource "google_redis_instance" "projects_chessworld_gcp_locations_us_central1_in" {
  name           = "projects/chessworld-gcp/locations/us-central1/instances/cloudmart-redis"
  region         = "us"
  tier           = "BASIC"
  memory_size_gb = 1
}

resource "google_storage_bucket" "cloudmart_assets_cf1ee46d" {
  name          = "cloudmart-assets-cf1ee46d"
  location      = "US"
  storage_class = "STANDARD"
  uniform_bucket_level_access = true
}

resource "google_storage_bucket" "cloudmart_images_cf1ee46d" {
  name          = "cloudmart-images-cf1ee46d"
  location      = "US"
  storage_class = "STANDARD"
  uniform_bucket_level_access = true
}

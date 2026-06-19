resource "google_compute_instance" "web_server" {
  name         = "web-server"
  machine_type = "n1-standard-2"
  zone         = "us-central1-a"

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-11"
    }
  }

  network_interface {
    network = "default"
    access_config {}  # ephemeral public IP
  }
}

resource "google_storage_bucket" "assets" {
  name     = "my-app-assets"
  location = "US"
}

resource "google_sql_database_instance" "postgres" {
  name             = "my-postgres"
  database_version = "POSTGRES_14"
  region           = "us-central1"

  settings {
    tier = "db-f1-micro"
  }
}

group "default" {
  targets = ["api", "bot", "cli"]
  context = "."
}

variable "REGISTRY" {
  default = "rg.fr-par.scw.cloud"
}

variable "TAG" {
  default = "local"
}

variable "VERSION" {
  default = "0.0.0"
}

target "docker-metadata-action" {}

target "_common" {
  inherits = ["docker-metadata-action"]
  labels = {
    "org.opencontainers.image.source" = "https://github.com/polarsen/polarsen"
    "org.opencontainers.image.author" = "bot@polarsen.com"
    "org.opencontainers.image.licenses" = "FSL-1.1-ALv2"
  }
  platforms = ["linux/amd64"]
  args = {
      VERSION = "${VERSION}"
  }
}

target "api" {
  inherits = ["_common"]
  dockerfile = "infra/api.Dockerfile"
  args = {
    PROJECT_MODE = "api"
  }
}

target "bot" {
  inherits = ["_common"]
  dockerfile = "infra/bot.Dockerfile"
  args = {
      PROJECT_MODE = "bot"
  }
}

target "cli" {
  inherits = ["_common"]
  dockerfile = "infra/cli.Dockerfile"
  args = {
      PROJECT_MODE = "cli"
  }
}

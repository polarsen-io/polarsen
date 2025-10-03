group "default" {
  targets = ["api", "bot", "cli"]
  context = "."
}

variable "REGISTRY" {
  default = "rg.fr-par.scw.cloud"
}

variable "TAG" {
  default = "latest"
}

variable "VERSION" {
  default = "0.0.0"
}


target "_common" {
  labels = {
    "org.opencontainers.image.source" = "https://github.com/polarsen/polarsen"
    "org.opencontainers.image.author" = "julien.brayere@polarsen.com"
  }
  platforms = ["linux/amd64"]
  args = {
      VERSION = "${VERSION}"
  }
}

target "api" {
  inherits = ["_common"]
  dockerfile = "infra/api.Dockerfile"
  tags = ["${REGISTRY}/polarsen/api:${TAG}"]
  args = {
    PROJECT_MODE = "api"
  }
}

target "bot" {
  inherits = ["_common"]
  dockerfile = "infra/bot.Dockerfile"
  tags = ["${REGISTRY}/polarsen/bot:${TAG}"]
  args = {
      PROJECT_MODE = "bot"
  }
}

target "cli" {
  inherits = ["_common"]
  dockerfile = "infra/cli.Dockerfile"
  tags = ["${REGISTRY}/polarsen/cli:${TAG}"]
  args = {
      PROJECT_MODE = "cli"
  }
}
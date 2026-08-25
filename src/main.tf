terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.31.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "2.7.1"
    }
  }

  backend "s3" {
    bucket       = "amz-lab-techchallenge-pt3"
    key          = "techchallenge/auth/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "vpc_id" {
  type    = string
  default = "vpc-0b8ea6480aff3581d"
}

variable "subnet_ids" {
  type    = list(string)
  default = ["subnet-01799be41421baa8d", "subnet-0d850838bbd1a4e72", "subnet-0a6b760a3d564676b"]
}

variable "security_group_id" {
  type    = string
  default = "sg-0471a65dcdc5a7dd5"
}

variable "rds_password" {
  type    = string
  default = "minha-senha"
}

variable "lambda_role_name" {
  type    = string
  default = "LabRole"
}

variable "rds_identifier" {
  type    = string
  default = "techchallenge-mysql"
}

data "aws_db_instance" "main" {
  db_instance_identifier = var.rds_identifier
}

data "aws_iam_role" "lab" {
  name = var.lambda_role_name
}

resource "terraform_data" "auth_package" {
  triggers_replace = [
    filesha256("${path.module}/auth_handler.py"),
    filesha256("${path.module}/requirements.txt"),
  ]

  provisioner "local-exec" {
    interpreter = ["powershell.exe", "-Command"]
    command     = <<-EOT
      $ErrorActionPreference = "Stop"
      $package = "${path.module}/.lambda_auth"
      if (Test-Path $package) { Remove-Item -Recurse -Force $package }
      New-Item -ItemType Directory -Path $package | Out-Null
      Copy-Item "${path.module}/auth_handler.py" "$package/auth_handler.py"
      $packageWsl = (wsl wslpath -a $package).Trim()
      $requirementsWsl = (wsl wslpath -a "${path.module}/requirements.txt").Trim()
      wsl python3 -m pip install -r $requirementsWsl -t $packageWsl --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 --only-binary=:all:
    EOT
  }
}

data "archive_file" "package" {
  depends_on  = [terraform_data.auth_package]
  type        = "zip"
  source_dir  = "${path.module}/.lambda_auth"
  output_path = "${path.module}/auth_handler.zip"
}

data "archive_file" "authorizer_package" {
  type        = "zip"
  source_file = "${path.module}/authorizer_handler.py"
  output_path = "${path.module}/authorizer_handler.zip"
}

resource "aws_lambda_function" "auth" {
  function_name    = "auto-repara-auth"
  filename         = data.archive_file.package.output_path
  source_code_hash = data.archive_file.package.output_base64sha256
  handler          = "auth_handler.handler"
  runtime          = "python3.12"
  role             = data.aws_iam_role.lab.arn
  timeout          = 10

  environment {
    variables = {
      DB_HOST                = data.aws_db_instance.main.endpoint
      DB_PORT                = "3306"
      DB_NAME                = "Tests"
      DB_USER                = "root"
      DB_PASSWORD            = var.rds_password
      CLIENT_TABLE           = "cliente"
      CLIENT_CPF_COLUMN      = "documento"
      CLIENT_STATUS_COLUMN   = "Ativo"
      ACTIVE_CLIENT_STATUS   = "ativo"
      USER_TABLE             = "usuario"
      USER_USERNAME_COLUMN   = "usuario"
      USER_PASSWORD_COLUMN   = "senha"
      USER_STATUS_COLUMN     = "Ativo"
      USER_ROLE_COLUMN       = "perfil"
      ACTIVE_USER_STATUS     = "ativo"
      JWT_SECRET             = "sua-chave-super-secreta-muito-longa-para-256bits-change-me"
      JWT_ISSUER             = "GestaoAutoRepara"
      JWT_AUDIENCE           = "GestaoAutoReparaUsers"
      JWT_EXPIRATION_SECONDS = "3600"
    }
  }

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [var.security_group_id]
  }
}

resource "aws_lambda_function" "authorizer" {
  function_name    = "auto-repara-authorizer"
  filename         = data.archive_file.authorizer_package.output_path
  source_code_hash = data.archive_file.authorizer_package.output_base64sha256
  handler          = "authorizer_handler.handler"
  runtime          = "python3.12"
  role             = data.aws_iam_role.lab.arn
  timeout          = 5

  environment {
    variables = {
      JWT_SECRET   = "sua-chave-super-secreta-muito-longa-para-256bits-change-me"
      JWT_ISSUER   = "GestaoAutoRepara"
      JWT_AUDIENCE = "GestaoAutoReparaUsers"
    }
  }
}

output "lambda_function_arn" {
  value = aws_lambda_function.auth.arn
}

output "authorizer_function_arn" {
  value = aws_lambda_function.authorizer.arn
}

# Cheapest scalable footprint: ECS Fargate SPOT + SQS + S3 + (Neon/RDS) + (Upstash/ElastiCache).
# Target < $70/mo. This is a STARTING POINT — wire your VPC/subnets/secrets before apply.
terraform {
  required_providers { aws = { source = "hashicorp/aws", version = "~> 5.0" } }
}
provider "aws" { region = var.region }

variable "region"       { default = "us-east-1" }
variable "monthly_cap"  { default = 70 }
variable "subnets"      { type = list(string) }   # your private subnets
variable "security_group" { type = string }

# --- signal bus (managed; swap for Redis Streams locally) ---
resource "aws_sqs_queue" "signals" {
  name                       = "valor-signals"
  visibility_timeout_seconds = 60
  message_retention_seconds  = 86400
}

# --- artifacts / configs / MLflow store ---
resource "aws_s3_bucket" "artifacts" { bucket = "valor-evolver-artifacts" }

# --- container registry + cluster ---
resource "aws_ecr_repository" "evolver" { name = "valor-evolver" }
resource "aws_ecs_cluster" "this" { name = "valor-evolver" }

# --- one always-on Fargate SPOT task: api + loop consumer (cheapest) ---
resource "aws_ecs_task_definition" "evolver" {
  family                   = "valor-evolver"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"   # 0.25 vCPU
  memory                   = "512"   # 0.5 GB
  execution_role_arn       = aws_iam_role.exec.arn
  container_definitions = jsonencode([{
    name      = "evolver"
    image     = "${aws_ecr_repository.evolver.repository_url}:latest"
    essential = true
    portMappings = [{ containerPort = 8000 }]
    environment = [{ name = "QUEUE_URL", value = aws_sqs_queue.signals.id }]
  }])
}

resource "aws_ecs_service" "evolver" {
  name            = "evolver"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.evolver.arn
  desired_count   = 1
  capacity_provider_strategy { capacity_provider = "FARGATE_SPOT", weight = 1 }  # ~70% cheaper
  network_configuration {
    subnets          = var.subnets
    security_groups  = [var.security_group]
    assign_public_ip = false
  }
}

# Strong-model optimizer runs RARELY -> a scheduled Lambda or one-shot Fargate task,
# not always-on. (Define separately; keep the always-on footprint tiny.)

resource "aws_iam_role" "exec" {
  name = "valor-evolver-exec"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{ Action = "sts:AssumeRole", Effect = "Allow",
      Principal = { Service = "ecs-tasks.amazonaws.com" } }]
  })
}

# --- spend guardrail ---
resource "aws_budgets_budget" "cap" {
  name         = "valor-evolver-cap"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_cap)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
}

# DB: use Neon (free Postgres) or db.t4g.micro RDS. Redis: Upstash free tier or
# cache.t4g.micro ElastiCache. Both cheaper than always-on managed at this scale.

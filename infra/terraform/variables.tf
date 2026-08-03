variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-west-2"
}

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
  default     = "llm-gateway"
}

variable "cluster_version" {
  description = "Kubernetes version"
  type        = string
  default     = "1.30"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "gpu_instance_type" {
  description = "GPU instance type for vLLM nodes"
  type        = string
  default     = "g5.xlarge"  # 1x A10G, 24GB VRAM — good for 70B quantized
}

variable "gpu_node_count" {
  description = "Number of GPU nodes"
  type        = number
  default     = 1
}

variable "cpu_instance_type" {
  description = "CPU instance type for gateway nodes"
  type        = string
  default     = "t3.medium"
}

variable "cpu_node_min" {
  description = "Min CPU nodes"
  type        = number
  default     = 2
}

variable "cpu_node_max" {
  description = "Max CPU nodes"
  type        = number
  default     = 5
}

variable "environment" {
  description = "Environment tag"
  type        = string
  default     = "production"
}

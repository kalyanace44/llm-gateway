# VPC
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "${var.cluster_name}-vpc"
  cidr = var.vpc_cidr

  azs             = ["${var.region}a", "${var.region}b", "${var.region}c"]
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]

  enable_nat_gateway   = true
  single_nat_gateway   = true  # Cost optimization for non-HA
  enable_dns_hostnames = true

  # Tags required for EKS
  public_subnet_tags = {
    "kubernetes.io/role/elb"                      = 1
    "kubernetes.io/cluster/${var.cluster_name}"    = "shared"
  }

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb"             = 1
    "kubernetes.io/cluster/${var.cluster_name}"    = "shared"
  }

  tags = {
    Environment = var.environment
    Project     = "llm-gateway"
  }
}

# EKS Cluster
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = var.cluster_name
  cluster_version = var.cluster_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  cluster_endpoint_public_access = true

  # Cluster addons
  cluster_addons = {
    coredns    = { most_recent = true }
    kube-proxy = { most_recent = true }
    vpc-cni    = { most_recent = true }
  }

  # Node groups
  eks_managed_node_groups = {
    # CPU nodes — runs the gateway, Redis, Prometheus
    cpu = {
      name           = "cpu-nodes"
      instance_types = [var.cpu_instance_type]
      min_size       = var.cpu_node_min
      max_size       = var.cpu_node_max
      desired_size   = var.cpu_node_min

      labels = {
        role = "gateway"
      }
    }

    # GPU nodes — runs vLLM model serving
    gpu = {
      name           = "gpu-nodes"
      instance_types = [var.gpu_instance_type]
      min_size       = var.gpu_node_count
      max_size       = var.gpu_node_count
      desired_size   = var.gpu_node_count

      ami_type = "AL2_x86_64_GPU"

      labels = {
        role                         = "inference"
        "nvidia.com/gpu.present"     = "true"
      }

      taints = [
        {
          key    = "nvidia.com/gpu"
          value  = "true"
          effect = "NO_SCHEDULE"
        }
      ]
    }
  }

  tags = {
    Environment = var.environment
    Project     = "llm-gateway"
  }
}

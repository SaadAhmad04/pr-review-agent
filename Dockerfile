# Dockerfile for PR Review Agent GitHub Action
# Base: Python 3.11-slim (minimal Debian-based image)
FROM python:3.11-slim

# Install system dependencies
# - ripgrep: fast code search (required by context_search node)
# - git: clone the target repository (needed to review any repo)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ripgrep \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir pylint

# Copy the agent source code
COPY src/ /app/src/
COPY scripts/ /app/scripts/

# Verify installation
RUN python -c "from src.graph import run_pr_review; print('Import check: OK')" && \
    rg --version && \
    git --version && \
    pylint --version

# Set entrypoint to the action script
ENTRYPOINT ["python", "/app/scripts/run_action.py"]

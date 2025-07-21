# Use a minimal Python base image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Copy source and config files
COPY intg-trinnov/driver.py ./
COPY trinnov.json remote_ui_page.json ./

# Install ucapi from PyPI
RUN pip install --no-cache-dir ucapi

# Optional: set the port as an environment variable
ENV PORT=9085

# Run the integration
CMD ["python", "driver.py"]

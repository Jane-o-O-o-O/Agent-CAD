#!/bin/bash

# Define the list of images to process
images=("simpleyyt/manus-sandbox" "simpleyyt/manus-frontend" "simpleyyt/manus-backend")

# Iterate through each image
for image in "${images[@]}"; do
    # Create manifest
    docker manifest create "${image}:latest" "${image}:amd64" "${image}:arm64"
    
    # Add annotation for amd64 architecture
    docker manifest annotate "${image}:latest" "${image}:amd64" --os linux --arch amd64
    
    # Add annotation for arm64 architecture
    docker manifest annotate "${image}:latest" "${image}:arm64" --os linux --arch arm64
    
    # Push manifest
    docker manifest push "${image}:latest"
done

// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

package main

// AI Hub handler — env vars used:
//   models_repository   base directory for all models (default: /models)
//   model_directory     expected subdirectory name after ZIP extraction
//   model_type          passed to qai_hub_models fetch -r
//   model_name          passed to qai_hub_models fetch
//   quantization        passed to qai_hub_models fetch -p
//   chipset             passed to qai_hub_models fetch -c
//   version             optional; passed to qai_hub_models fetch -v

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

func runAIHub(action string) {
	switch action {
	case "download":
		aiHubDownload()
	case "check":
		aiHubCheck()
	case "remove":
		aiHubRemove()
	default:
		emitError(fmt.Sprintf("unknown action: %s", action))
		os.Exit(1)
	}
}

func aiHubModelsRepo() string {
	if v := os.Getenv("models_repository"); v != "" {
		return v
	}
	return "/models"
}

func aiHubDownload() {
	modelsRepo := aiHubModelsRepo()
	modelDir := os.Getenv("model_directory")
	modelType := os.Getenv("model_type")
	modelName := os.Getenv("model_name")
	quantization := os.Getenv("quantization")
	chipset := os.Getenv("chipset")
	version := os.Getenv("version")

	destDir := filepath.Join(modelsRepo, modelDir)
	if _, err := os.Stat(destDir); err == nil {
		emitInfo(fmt.Sprintf("Model exists: %s", modelDir))
		return
	}

	// Ask qai_hub_models for the download URL only
	args := []string{"fetch", modelName, "-r", modelType, "-p", quantization, "-c", chipset}
	if version != "" {
		args = append(args, "-v", version)
	}
	args = append(args, "--url-only")

	cmd := exec.Command("qai_hub_models", args...)
	out, err := cmd.Output()
	if err != nil {
		stderr := ""
		if ee, ok := err.(*exec.ExitError); ok {
			stderr = strings.TrimSpace(string(ee.Stderr))
		}
		emitError(fmt.Sprintf("Failed to fetch model URL: %s", stderr))
		os.Exit(1)
	}

	url := strings.TrimSpace(string(out))
	if url == "" || !strings.HasPrefix(url, "http") {
		emitError(fmt.Sprintf("Invalid URL from qai_hub_models: %q", url))
		os.Exit(1)
	}

	emitInfo(fmt.Sprintf("Downloading model from: %s", url))

	if err := downloadAndExtract(newHTTPClient(), url, modelsRepo, nil); err != nil {
		emitError(fmt.Sprintf("Failed to download model %s: %v", modelName, err))
		os.Exit(1)
	}
}

func aiHubCheck() {
	modelsRepo := aiHubModelsRepo()
	modelDir := os.Getenv("model_directory")
	destDir := filepath.Join(modelsRepo, modelDir)
	if _, err := os.Stat(destDir); err == nil {
		emitInfo(fmt.Sprintf("Model exists: %s", modelDir))
	} else {
		emitError(fmt.Sprintf("Model does not exist: %s", modelDir))
		os.Exit(1)
	}
}

func aiHubRemove() {
	modelsRepo := aiHubModelsRepo()
	modelDir := os.Getenv("model_directory")
	destDir := filepath.Join(modelsRepo, modelDir)
	if err := os.RemoveAll(destDir); err != nil {
		emitError(fmt.Sprintf("Failed to remove model: %s", modelDir))
		os.Exit(1)
	}
	emitInfo(fmt.Sprintf("Model removed: %s", modelDir))
}

// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

package main

// AI Hub handler — env vars used:
//   models_repository   base directory for all models (default: /models)
//   model_directory     expected subdirectory name after ZIP extraction
//   model_name          model ID, e.g. melotts_en
//   model_type          runtime, e.g. voice_ai, genie, qnn_dlc
//   quantization        precision, e.g. mixed_with_float, w4a16, w8a16
//   chipset             e.g. qualcomm-qcs8275  (hyphens are converted to underscores)
//   version             release version, e.g. 0.51.0

// Models are served from a public S3 bucket — no credentials required.
// URL template (chipset-specific):
//   https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-models/models/
//   {model_name}/releases/v{version}/{model_name}-{model_type}-{quantization}-{chipset}.zip
// Fallback (no chipset):
//   .../v{version}/{model_name}-{model_type}-{quantization}.zip

import (
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

const aiHubS3Base = "https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-models/models"

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

// aiHubResolveURL constructs and verifies the S3 download URL.
// It tries the chipset-specific asset first, then falls back to the generic one.
func aiHubResolveURL(client *http.Client, modelName, modelType, quantization, chipset, version string) (string, error) {
	return aiHubResolveURLBase(client, modelName, modelType, quantization, chipset, version, aiHubS3Base)
}

func aiHubResolveURLBase(client *http.Client, modelName, modelType, quantization, chipset, version, s3Base string) (string, error) {
	chipsetUnderscored := strings.ReplaceAll(chipset, "-", "_")
	base := fmt.Sprintf("%s/%s/releases/v%s", s3Base, modelName, version)

	candidates := []string{
		fmt.Sprintf("%s/%s-%s-%s-%s.zip", base, modelName, modelType, quantization, chipsetUnderscored),
		fmt.Sprintf("%s/%s-%s-%s.zip", base, modelName, modelType, quantization),
	}

	for _, url := range candidates {
		resp, err := client.Head(url)
		if err != nil {
			continue
		}
		resp.Body.Close()
		if resp.StatusCode == http.StatusOK {
			return url, nil
		}
	}

	return "", fmt.Errorf("no asset found for model=%q type=%q quantization=%q chipset=%q version=%q — check https://aihub.qualcomm.com/models",
		modelName, modelType, quantization, chipset, version)
}

func aiHubDownload() {
	modelsRepo := aiHubModelsRepo()
	modelDir := os.Getenv("model_directory")
	modelName := os.Getenv("model_name")
	modelType := os.Getenv("model_type")
	quantization := os.Getenv("quantization")
	chipset := os.Getenv("chipset")
	version := os.Getenv("version")

	if modelName == "" || modelType == "" || quantization == "" || chipset == "" || version == "" {
		emitError("Missing required env vars: model_name, model_type, quantization, chipset, version")
		os.Exit(1)
	}

	destDir := filepath.Join(modelsRepo, modelDir)
	if _, err := os.Stat(destDir); err == nil {
		emitInfo(fmt.Sprintf("Model exists: %s", modelDir))
		return
	}

	client := newHTTPClient()
	url, err := aiHubResolveURL(client, modelName, modelType, quantization, chipset, version)
	if err != nil {
		emitError(err.Error())
		os.Exit(1)
	}

	emitInfo(fmt.Sprintf("Downloading model from: %s", url))

	if err := downloadAndExtract(client, url, modelsRepo, nil); err != nil {
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

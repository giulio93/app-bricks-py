// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

package main

// Edge Impulse handler — env vars used:
//   models_repository   base directory for all models (default: /models)
//   ei_project_id       Edge Impulse project ID
//   ei_impulse_id       impulse ID
//   model_name          output filename (saved as models_repository/model_name)
//   quantization        e.g. float32, int8
//   target              e.g. runner-linux-aarch64, runner-linux-aarch64-qnn

import (
	"fmt"
	"os"
	"path/filepath"
)

const eiAPIURL = "https://studio.edgeimpulse.com/v1/api/%s/deployment/download?type=%s&modelType=%s&impulseId=%s"

func runEI(action string) {
	switch action {
	case "download":
		eiDownload()
	case "check":
		eiCheck()
	case "remove":
		eiRemove()
	default:
		emitError(fmt.Sprintf("unknown action: %s", action))
		os.Exit(1)
	}
}

func eiModelsRepo() string {
	if v := os.Getenv("models_repository"); v != "" {
		return v
	}
	return "/models"
}

func eiDownload() {
	modelsRepo := eiModelsRepo()
	projectID := os.Getenv("ei_project_id")
	impulseID := os.Getenv("ei_impulse_id")
	modelName := os.Getenv("model_name")
	quantization := os.Getenv("quantization")
	target := os.Getenv("target")

	if projectID == "" || impulseID == "" || modelName == "" || quantization == "" || target == "" {
		emitError("Missing required env vars: ei_project_id, ei_impulse_id, model_name, quantization, target")
		os.Exit(1)
	}

	outPath := filepath.Join(modelsRepo, modelName)
	if _, err := os.Stat(outPath); err == nil {
		emitInfo(fmt.Sprintf("Model exists: %s", modelName))
		return
	}

	url := fmt.Sprintf(eiAPIURL, projectID, target, quantization, impulseID)

	saved, err := downloadFile(newHTTPClient(), url, modelsRepo, modelName, nil)
	if err != nil {
		emitError(fmt.Sprintf("Failed to download model %s: %v", modelName, err))
		os.Exit(1)
	}

	if err := os.Chmod(saved, 0o755); err != nil {
		emitError(fmt.Sprintf("Failed to chmod model file: %v", err))
		os.Exit(1)
	}
}

func eiCheck() {
	modelsRepo := eiModelsRepo()
	modelName := os.Getenv("model_name")
	modelPath := filepath.Join(modelsRepo, modelName)
	if _, err := os.Stat(modelPath); err == nil {
		emitInfo(fmt.Sprintf("Model exists: %s", modelName))
	} else {
		emitError(fmt.Sprintf("Model does not exist: %s", modelName))
		os.Exit(1)
	}
}

func eiRemove() {
	modelsRepo := eiModelsRepo()
	modelName := os.Getenv("model_name")
	modelPath := filepath.Join(modelsRepo, modelName)
	if err := os.RemoveAll(modelPath); err != nil {
		emitError(fmt.Sprintf("Failed to remove model: %s", modelName))
		os.Exit(1)
	}
	emitInfo(fmt.Sprintf("Model removed: %s", modelName))
}

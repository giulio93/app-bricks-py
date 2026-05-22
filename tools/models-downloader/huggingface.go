// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

package main

// HuggingFace handler — env vars used:
//   models_repository   base directory for all models (default: /models)
//   hf_token            optional HF API token for gated/private repos
//
// Model selection — either:
//   model_key           compact key: <type>:<repo_id>:<quantization>[:<mmproj_quantization>]
//                       e.g. llamacpp:unsloth/gemma-4-E4B-it-GGUF:Q4_0:BF16
// or:
//   model_repo_id       e.g. unsloth/gemma-4-E4B-it-GGUF
//   model_name          filename or fnmatch pattern (*.gguf suffix added if absent)
//   model_mmproj_name   optional mmproj filename/pattern

import (
	"encoding/json"
	"fmt"
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

func runHF(action string) {
	switch action {
	case "download":
		hfDownload()
	case "check":
		hfCheck()
	case "remove":
		hfRemove()
	default:
		emitError(fmt.Sprintf("unknown action: %s", action))
		os.Exit(1)
	}
}

type hfParams struct {
	repoID       string
	allowPattern string
	mmproj       string
	modelsRepo   string
	token        string
}

func hfResolveParams() hfParams {
	modelsRepo := os.Getenv("models_repository")
	if modelsRepo == "" {
		modelsRepo = "/models"
	}
	p := hfParams{modelsRepo: modelsRepo, token: os.Getenv("hf_token")}

	if key := os.Getenv("model_key"); key != "" {
		// format: <type>:<repo_id>:<quantization>[:<mmproj_quantization>]
		parts := strings.SplitN(key, ":", 4)
		if len(parts) < 3 || parts[1] == "" || parts[2] == "" {
			emitError(fmt.Sprintf("invalid model_key %q — expected <type>:<repo_id>:<quantization>[:<mmproj_quant>]", key))
			os.Exit(1)
		}
		p.repoID = parts[1]
		p.allowPattern = fmt.Sprintf("*%s*.gguf", parts[2])
		if len(parts) == 4 && parts[3] != "" {
			p.mmproj = fmt.Sprintf("*mmproj*%s*.gguf", parts[3])
		}
	} else {
		p.repoID = os.Getenv("model_repo_id")
		modelName := os.Getenv("model_name")
		if p.repoID == "" || modelName == "" {
			emitError("Missing required env vars: model_key or (model_repo_id + model_name)")
			os.Exit(1)
		}
		p.allowPattern = toGlobPattern(modelName)
		if mm := os.Getenv("model_mmproj_name"); mm != "" {
			p.mmproj = toGlobPattern(mm)
		}
	}
	return p
}

func toGlobPattern(name string) string {
	if strings.ContainsAny(name, "*?") {
		return name
	}
	if strings.HasSuffix(name, ".gguf") {
		return name
	}
	return fmt.Sprintf("*%s*", name)
}

const hfAPIURLFmt = "https://huggingface.co/api/models/%s"
const hfResolveURLFmt = "https://huggingface.co/%s/resolve/main/%s"

// hfListFiles queries the HuggingFace API and returns all filenames in the repo.
func hfListFiles(client *http.Client, repoID, token string) ([]string, error) {
	return hfListFilesURL(client, repoID, token, hfAPIURLFmt)
}

func hfListFilesURL(client *http.Client, repoID, token, urlFmt string) ([]string, error) {
	req, err := http.NewRequest("GET", fmt.Sprintf(urlFmt, repoID), nil)
	if err != nil {
		return nil, err
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}

	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("HF API request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HF API HTTP %d %s", resp.StatusCode, resp.Status)
	}

	var result struct {
		Siblings []struct {
			RFilename string `json:"rfilename"`
		} `json:"siblings"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("HF API decode: %w", err)
	}

	files := make([]string, 0, len(result.Siblings))
	for _, s := range result.Siblings {
		files = append(files, s.RFilename)
	}
	return files, nil
}


func globMatch(pattern, name string) bool {
	matched, _ := filepath.Match(pattern, name)
	return matched
}

// hfFilterFiles splits repo files into main model files and mmproj files.
// mmproj files are excluded from the main list even if they match allowPattern.
func hfFilterFiles(files []string, allowPattern, mmprojPattern string) (main []string, mmproj []string) {
	for _, f := range files {
		base := filepath.Base(f)
		if globMatch(allowPattern, base) && !strings.HasPrefix(base, "mmproj") {
			main = append(main, f)
		}
	}
	if mmprojPattern != "" {
		for _, f := range files {
			if globMatch(mmprojPattern, filepath.Base(f)) {
				mmproj = append(mmproj, f)
			}
		}
	}
	return
}

func hfDownloadFiles(client *http.Client, repoID, token, modelsRepo string, filenames []string) error {
	headers := map[string]string{}
	if token != "" {
		headers["Authorization"] = "Bearer " + token
	}
	repoOutputDir := filepath.Join(modelsRepo, repoID)
	for _, filename := range filenames {
		url := fmt.Sprintf("https://huggingface.co/%s/resolve/main/%s", repoID, filename)
		if _, err := downloadFile(client, url, repoOutputDir, filepath.Base(filename), headers); err != nil {
			return fmt.Errorf("download %s: %w", filename, err)
		}
	}
	return nil
}

func generateModelsIni(modelsDir string) error {
	type iniEntry struct {
		stem   string
		model  string
		mmproj string
	}
	var entries []iniEntry

	err := filepath.WalkDir(modelsDir, func(path string, d fs.DirEntry, werr error) error {
		if werr != nil || d.IsDir() {
			return werr
		}
		name := d.Name()
		if !strings.HasSuffix(name, ".gguf") || strings.HasPrefix(name, "mmproj") {
			return nil
		}
		e := iniEntry{
			stem:  strings.TrimSuffix(name, ".gguf"),
			model: filepath.ToSlash(path),
		}
		dirEntries, _ := os.ReadDir(filepath.Dir(path))
		for _, de := range dirEntries {
			if strings.HasPrefix(de.Name(), "mmproj") && strings.HasSuffix(de.Name(), ".gguf") {
				e.mmproj = filepath.ToSlash(filepath.Join(filepath.Dir(path), de.Name()))
				break
			}
		}
		entries = append(entries, e)
		return nil
	})
	if err != nil {
		return err
	}

	sort.Slice(entries, func(i, j int) bool { return entries[i].stem < entries[j].stem })

	outPath := filepath.Join(modelsDir, "models.ini")
	f, err := os.Create(outPath)
	if err != nil {
		return err
	}
	defer f.Close()

	for _, e := range entries {
		fmt.Fprintf(f, "[%s]\n", e.stem)
		fmt.Fprintf(f, "model = %s\n", e.model)
		if e.mmproj != "" {
			fmt.Fprintf(f, "mmproj = %s\n", e.mmproj)
		}
		fmt.Fprintln(f)
	}

	emitInfo(fmt.Sprintf("Generated models.ini with %d model(s)", len(entries)), outPath)
	return nil
}

func hfDownload() {
	p := hfResolveParams()
	client := newHTTPClient()

	files, err := hfListFiles(client, p.repoID, p.token)
	if err != nil {
		emitError(fmt.Sprintf("Failed to list HF repo files: %v", err))
		os.Exit(1)
	}

	mainFiles, mmprojFiles := hfFilterFiles(files, p.allowPattern, p.mmproj)
	if len(mainFiles) == 0 {
		emitError(fmt.Sprintf("No files matching %q in repo %s", p.allowPattern, p.repoID))
		os.Exit(1)
	}

	if err := hfDownloadFiles(client, p.repoID, p.token, p.modelsRepo, mainFiles); err != nil {
		emitError(fmt.Sprintf("Download failed: %v", err))
		os.Exit(1)
	}
	if len(mmprojFiles) > 0 {
		if err := hfDownloadFiles(client, p.repoID, p.token, p.modelsRepo, mmprojFiles); err != nil {
			emitError(fmt.Sprintf("MMProj download failed: %v", err))
			os.Exit(1)
		}
	}

	if err := generateModelsIni(p.modelsRepo); err != nil {
		emitError(fmt.Sprintf("Failed to generate models.ini: %v", err))
		os.Exit(1)
	}
}

func hfCheck() {
	p := hfResolveParams()
	repoDir := filepath.Join(p.modelsRepo, p.repoID)

	var matched []string
	_ = filepath.WalkDir(repoDir, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return nil
		}
		name := d.Name()
		if globMatch(p.allowPattern, name) || (p.mmproj != "" && globMatch(p.mmproj, name)) {
			matched = append(matched, path)
		}
		return nil
	})

	if len(matched) > 0 {
		emitInfo(fmt.Sprintf("Model exists: %s", p.allowPattern))
	} else {
		emitError(fmt.Sprintf("Model does not exist: %s", p.allowPattern))
		os.Exit(1)
	}
}

func hfRemove() {
	p := hfResolveParams()
	repoDir := filepath.Join(p.modelsRepo, p.repoID)

	var toDelete []string
	_ = filepath.WalkDir(repoDir, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return nil
		}
		name := d.Name()
		if globMatch(p.allowPattern, name) || (p.mmproj != "" && globMatch(p.mmproj, name)) {
			toDelete = append(toDelete, path)
		}
		return nil
	})

	for _, f := range toDelete {
		os.Remove(f)
	}

	// Remove empty directories bottom-up
	var dirs []string
	_ = filepath.WalkDir(repoDir, func(path string, d fs.DirEntry, err error) error {
		if err == nil && d.IsDir() && path != p.modelsRepo {
			dirs = append(dirs, path)
		}
		return nil
	})
	sort.Slice(dirs, func(i, j int) bool {
		return strings.Count(dirs[i], string(os.PathSeparator)) > strings.Count(dirs[j], string(os.PathSeparator))
	})
	for _, dir := range dirs {
		os.Remove(dir) // succeeds only if empty
	}

	if err := generateModelsIni(p.modelsRepo); err != nil {
		emitError(fmt.Sprintf("Failed to generate models.ini: %v", err))
		os.Exit(1)
	}
}

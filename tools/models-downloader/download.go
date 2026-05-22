// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

package main

import (
	"archive/zip"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

const chunkSize = 1024 * 1024 // 1 MiB

func newHTTPClient() *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.ResponseHeaderTimeout = 60 * time.Second
	return &http.Client{Transport: transport}
}

func filenameFromResponse(resp *http.Response, fallback string) string {
	cd := resp.Header.Get("Content-Disposition")
	if idx := strings.Index(cd, "filename="); idx >= 0 {
		name := strings.Trim(cd[idx+len("filename="):], `"' `)
		if name != "" {
			return name
		}
	}
	return fallback
}

func urlBasename(url string) string {
	url = strings.TrimRight(url, "/")
	if i := strings.LastIndex(url, "/"); i >= 0 {
		return url[i+1:]
	}
	return "download"
}

// downloadFile fetches url and saves it to outputDir/outputName (or a name derived from the response).
// Returns the saved file path.
func downloadFile(client *http.Client, url, outputDir, outputName string, headers map[string]string) (string, error) {
	// Skip the HTTP request entirely if we already know the target filename.
	if outputName != "" {
		if err := os.MkdirAll(outputDir, 0o755); err != nil {
			return "", fmt.Errorf("mkdir %s: %w", outputDir, err)
		}
		outPath := filepath.Join(outputDir, outputName)
		if _, err := os.Stat(outPath); err == nil {
			emitProgress("info", fmt.Sprintf("File already exists: %s", outPath), 0, 0, "B", outPath)
			return outPath, nil
		}
	}

	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return "", err
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}

	resp, err := client.Do(req)
	if err != nil {
		return "", fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("HTTP %d %s", resp.StatusCode, resp.Status)
	}

	filename := outputName
	if filename == "" {
		filename = filenameFromResponse(resp, urlBasename(url))
	}
	if err := os.MkdirAll(outputDir, 0o755); err != nil {
		return "", fmt.Errorf("mkdir %s: %w", outputDir, err)
	}

	outPath := filepath.Join(outputDir, filename)

	var total int64
	if cl := resp.Header.Get("Content-Length"); cl != "" {
		total, _ = strconv.ParseInt(cl, 10, 64)
	}

	if _, err := os.Stat(outPath); err == nil {
		emitProgress("info", fmt.Sprintf("File already exists: %s", outPath), total, total, "B", outPath)
		return outPath, nil
	}

	f, err := os.Create(outPath)
	if err != nil {
		return "", fmt.Errorf("create %s: %w", outPath, err)
	}
	defer f.Close()

	emitProgress("start", fmt.Sprintf("Downloading %s from %s", filename, url), 0, total, "B")

	buf := make([]byte, chunkSize)
	var downloaded int64
	lastEmit := time.Now()

	for {
		n, readErr := resp.Body.Read(buf)
		if n > 0 {
			if _, werr := f.Write(buf[:n]); werr != nil {
				return "", fmt.Errorf("write: %w", werr)
			}
			downloaded += int64(n)
			if time.Since(lastEmit) >= time.Second {
				emitProgress("update", fmt.Sprintf("Downloading %s", filename), downloaded, total, "B")
				lastEmit = time.Now()
			}
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			return "", fmt.Errorf("read: %w", readErr)
		}
	}

	emitProgress("complete", fmt.Sprintf("Downloaded %s from %s", filename, url), downloaded, total, "B", outPath)
	return outPath, nil
}

// downloadAndExtract fetches url as a ZIP and extracts its contents to outputDir.
// It buffers to a temp file first because archive/zip requires io.ReaderAt.
func downloadAndExtract(client *http.Client, url, outputDir string, headers map[string]string) error {
	if err := os.MkdirAll(outputDir, 0o755); err != nil {
		return fmt.Errorf("mkdir %s: %w", outputDir, err)
	}

	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return err
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}

	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d %s", resp.StatusCode, resp.Status)
	}

	filename := filenameFromResponse(resp, urlBasename(url))
	var total int64
	if cl := resp.Header.Get("Content-Length"); cl != "" {
		total, _ = strconv.ParseInt(cl, 10, 64)
	}

	tmp, err := os.CreateTemp(outputDir, "download-*.zip")
	if err != nil {
		return fmt.Errorf("temp file: %w", err)
	}
	tmpPath := tmp.Name()
	defer os.Remove(tmpPath)

	emitProgress("start", fmt.Sprintf("Downloading %s from %s", filename, url), 0, total, "B")

	buf := make([]byte, chunkSize)
	var downloaded int64
	lastEmit := time.Now()

	for {
		n, readErr := resp.Body.Read(buf)
		if n > 0 {
			if _, werr := tmp.Write(buf[:n]); werr != nil {
				tmp.Close()
				return fmt.Errorf("write: %w", werr)
			}
			downloaded += int64(n)
			if time.Since(lastEmit) >= time.Second {
				emitProgress("update", fmt.Sprintf("Downloading %s", filename), downloaded, total, "B")
				lastEmit = time.Now()
			}
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			tmp.Close()
			return fmt.Errorf("read: %w", readErr)
		}
	}
	tmp.Close()

	emitProgress("complete", fmt.Sprintf("Downloaded %s from %s", filename, url), downloaded, total, "B")
	emitInfo(fmt.Sprintf("Extracting %s to %s", filename, outputDir))

	zr, err := zip.OpenReader(tmpPath)
	if err != nil {
		return fmt.Errorf("open zip: %w", err)
	}
	defer zr.Close()

	cleanOutput := filepath.Clean(outputDir)
	var artifacts []string

	for _, entry := range zr.File {
		destPath := filepath.Join(outputDir, entry.Name)

		// Prevent path traversal attacks
		rel, err := filepath.Rel(cleanOutput, filepath.Clean(destPath))
		if err != nil || strings.HasPrefix(rel, "..") {
			return fmt.Errorf("zip path traversal: %s", entry.Name)
		}

		if entry.FileInfo().IsDir() {
			os.MkdirAll(destPath, 0o755)
			continue
		}
		if err := os.MkdirAll(filepath.Dir(destPath), 0o755); err != nil {
			return fmt.Errorf("mkdir: %w", err)
		}
		out, err := os.Create(destPath)
		if err != nil {
			return fmt.Errorf("create %s: %w", destPath, err)
		}
		rc, err := entry.Open()
		if err != nil {
			out.Close()
			return fmt.Errorf("open zip entry: %w", err)
		}
		_, copyErr := io.Copy(out, rc)
		rc.Close()
		out.Close()
		if copyErr != nil {
			return fmt.Errorf("extract %s: %w", entry.Name, copyErr)
		}
		artifacts = append(artifacts, destPath)
	}

	emit(progressEvent{
		Event:       "complete",
		Description: fmt.Sprintf("Extracted to: %s", outputDir),
		Artifacts:   artifacts,
	})
	return nil
}

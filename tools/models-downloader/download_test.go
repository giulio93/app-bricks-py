// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

package main

import (
	"archive/zip"
	"bytes"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

// makeZip builds an in-memory ZIP with the given name→content entries.
func makeZip(t *testing.T, entries map[string]string) []byte {
	t.Helper()
	var buf bytes.Buffer
	w := zip.NewWriter(&buf)
	for name, content := range entries {
		f, err := w.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		fmt.Fprint(f, content)
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

func TestDownloadFile(t *testing.T) {
	want := "hello model"
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Disposition", `attachment; filename="model.bin"`)
		fmt.Fprint(w, want)
	}))
	defer srv.Close()

	dir := t.TempDir()
	path, err := downloadFile(srv.Client(), srv.URL+"/model.bin", dir, "", nil)
	if err != nil {
		t.Fatalf("downloadFile: %v", err)
	}
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != want {
		t.Errorf("got %q, want %q", got, want)
	}
	if filepath.Base(path) != "model.bin" {
		t.Errorf("unexpected filename: %s", filepath.Base(path))
	}
}

func TestDownloadFile_OutputNameOverride(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, "data")
	}))
	defer srv.Close()

	dir := t.TempDir()
	path, err := downloadFile(srv.Client(), srv.URL+"/ignored", dir, "my-model.eim", nil)
	if err != nil {
		t.Fatalf("downloadFile: %v", err)
	}
	if filepath.Base(path) != "my-model.eim" {
		t.Errorf("expected my-model.eim, got %s", filepath.Base(path))
	}
}

func TestDownloadFile_AlreadyExists(t *testing.T) {
	calls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		fmt.Fprint(w, "data")
	}))
	defer srv.Close()

	dir := t.TempDir()
	// Pre-create the file
	if err := os.WriteFile(filepath.Join(dir, "model.bin"), []byte("existing"), 0o644); err != nil {
		t.Fatal(err)
	}

	_, err := downloadFile(srv.Client(), srv.URL+"/model.bin", dir, "model.bin", nil)
	if err != nil {
		t.Fatal(err)
	}
	if calls != 0 {
		t.Error("expected no HTTP request when file already exists")
	}
}

func TestDownloadFile_HTTPError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "not found", http.StatusNotFound)
	}))
	defer srv.Close()

	_, err := downloadFile(srv.Client(), srv.URL+"/x", t.TempDir(), "", nil)
	if err == nil {
		t.Fatal("expected error on 404")
	}
}

func TestDownloadAndExtract(t *testing.T) {
	zipData := makeZip(t, map[string]string{
		"subdir/file1.txt": "content1",
		"file2.txt":        "content2",
	})

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/zip")
		w.Header().Set("Content-Disposition", `attachment; filename="archive.zip"`)
		w.Write(zipData)
	}))
	defer srv.Close()

	dir := t.TempDir()
	if err := downloadAndExtract(srv.Client(), srv.URL+"/archive.zip", dir, nil); err != nil {
		t.Fatalf("downloadAndExtract: %v", err)
	}

	for _, f := range []string{"subdir/file1.txt", "file2.txt"} {
		if _, err := os.Stat(filepath.Join(dir, f)); err != nil {
			t.Errorf("expected extracted file %s: %v", f, err)
		}
	}
}

func TestDownloadAndExtract_PathTraversal(t *testing.T) {
	zipData := makeZip(t, map[string]string{
		"../evil.txt": "pwned",
	})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write(zipData)
	}))
	defer srv.Close()

	err := downloadAndExtract(srv.Client(), srv.URL+"/evil.zip", t.TempDir(), nil)
	if err == nil {
		t.Fatal("expected path-traversal error")
	}
}

func TestFilenameFromResponse(t *testing.T) {
	cases := []struct {
		header   string
		fallback string
		want     string
	}{
		{`attachment; filename="model.zip"`, "fallback.zip", "model.zip"},
		{`attachment; filename='model.zip'`, "fallback.zip", "model.zip"},
		{"", "fallback.zip", "fallback.zip"},
		{"attachment; no-filename", "fallback.zip", "fallback.zip"},
	}
	for _, c := range cases {
		resp := &http.Response{Header: http.Header{}}
		if c.header != "" {
			resp.Header.Set("Content-Disposition", c.header)
		}
		got := filenameFromResponse(resp, c.fallback)
		if got != c.want {
			t.Errorf("header=%q: got %q, want %q", c.header, got, c.want)
		}
	}
}

// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

package main

import (
	"encoding/json"
	"fmt"
)

type progressEvent struct {
	Event       string   `json:"event"`
	Description string   `json:"description"`
	Current     int64    `json:"current,omitempty"`
	Total       int64    `json:"total,omitempty"`
	Unit        string   `json:"unit,omitempty"`
	Percentage  string   `json:"percentage,omitempty"`
	Artifacts   []string `json:"artifacts,omitempty"`
}

func emit(ev progressEvent) {
	b, _ := json.Marshal(ev)
	fmt.Println(string(b))
}

func emitInfo(desc string, artifacts ...string) {
	ev := progressEvent{Event: "info", Description: desc}
	if len(artifacts) > 0 {
		ev.Artifacts = artifacts
	}
	emit(ev)
}

func emitError(desc string) {
	emit(progressEvent{Event: "error", Description: desc})
}

func emitProgress(eventType, desc string, current, total int64, unit string, artifacts ...string) {
	var pct float64
	if total > 0 {
		pct = float64(current) / float64(total) * 100
	}
	ev := progressEvent{
		Event:       eventType,
		Description: desc,
		Current:     current,
		Total:       total,
		Unit:        unit,
		Percentage:  fmt.Sprintf("%.2f%%", pct),
	}
	if len(artifacts) > 0 {
		ev.Artifacts = artifacts
	}
	emit(ev)
}

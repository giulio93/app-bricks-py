// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

package main

import (
	"fmt"
	"os"
)

func usage() {
	fmt.Fprintf(os.Stderr, `Usage: models-downloader <handler> <action>

Handlers and actions:
  ai-hub   download | check | remove
  ei       download | check | remove
  hf       download | check | remove

Parameters are passed via environment variables (same convention as the shell scripts).
`)
}

func main() {
	if len(os.Args) < 3 {
		usage()
		os.Exit(1)
	}

	handler := os.Args[1]
	action := os.Args[2]

	switch handler {
	case "ai-hub":
		runAIHub(action)
	case "ei":
		runEI(action)
	case "hf":
		runHF(action)
	default:
		emitError(fmt.Sprintf("unknown handler: %s", handler))
		os.Exit(1)
	}
}

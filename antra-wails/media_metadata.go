package main

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

type LyricsLine struct {
	TimeMs int64  `json:"time_ms"`
	Text   string `json:"text"`
}

type TrackLyrics struct {
	Lines  []LyricsLine `json:"lines"`
	Synced bool         `json:"synced"`
}

var reLRCTimestamp = regexp.MustCompile(`^\[(\d+):(\d{2})\.(\d{2,3})\](.*)$`)

func parseLRC(raw string) []LyricsLine {
	var lines []LyricsLine
	for _, line := range strings.Split(raw, "\n") {
		line = strings.TrimRight(line, "\r")
		match := reLRCTimestamp.FindStringSubmatch(strings.TrimSpace(line))
		if match == nil {
			continue
		}
		mins, _ := strconv.ParseInt(match[1], 10, 64)
		secs, _ := strconv.ParseInt(match[2], 10, 64)
		frac := match[3]
		ms, _ := strconv.ParseInt(frac, 10, 64)
		if len(frac) == 2 {
			ms *= 10
		}
		text := strings.TrimSpace(match[4])
		if text != "" {
			lines = append(lines, LyricsLine{TimeMs: (mins*60+secs)*1000 + ms, Text: text})
		}
	}
	sort.Slice(lines, func(i, j int) bool { return lines[i].TimeMs < lines[j].TimeMs })
	return lines
}

var lyricsTagKeys = []string{
	"LYRICS", "lyrics", "Lyrics",
	"SYNCEDLYRICS", "syncedlyrics",
	"UNSYNCEDLYRICS", "unsyncedlyrics",
	"LYRICS:LYRICS",
}

func (a *App) GetTrackLyrics(filePath string) string {
	a.mu.Lock()
	ffprobeExe := a.ffprobeExe
	a.mu.Unlock()

	cmd := exec.Command(
		resolveExe(ffprobeExe, "ffprobe"),
		"-v", "quiet",
		"-print_format", "json",
		"-show_format",
		filePath,
	)
	hideProcess(cmd)
	out, err := cmd.Output()
	if err != nil {
		return `{"lines":[],"synced":false}`
	}

	var probe struct {
		Format struct {
			Tags map[string]string `json:"tags"`
		} `json:"format"`
	}
	if err := json.Unmarshal(out, &probe); err != nil {
		return `{"lines":[],"synced":false}`
	}

	raw := ""
	for _, key := range lyricsTagKeys {
		if value, ok := probe.Format.Tags[key]; ok && strings.TrimSpace(value) != "" {
			raw = value
			break
		}
	}
	if raw == "" {
		return `{"lines":[],"synced":false}`
	}

	result := TrackLyrics{}
	if lines := parseLRC(raw); len(lines) > 0 {
		result.Synced = true
		result.Lines = lines
	} else {
		for _, line := range strings.Split(raw, "\n") {
			result.Lines = append(result.Lines, LyricsLine{TimeMs: -1, Text: strings.TrimRight(line, "\r")})
		}
	}

	encoded, err := json.Marshal(result)
	if err != nil {
		return `{"lines":[],"synced":false}`
	}
	return string(encoded)
}

func resolveExe(exePath, name string) string {
	if exePath != "" {
		return exePath
	}
	return name
}

func runFFProbe(filePath, ffprobeExe string) (map[string]interface{}, error) {
	cmd := exec.Command(
		resolveExe(ffprobeExe, "ffprobe"),
		"-v", "quiet",
		"-print_format", "json",
		"-show_format",
		"-show_streams",
		"-select_streams", "a:0",
		filePath,
	)
	hideProcess(cmd)
	output, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("ffprobe: %w", err)
	}

	var data map[string]interface{}
	if err := json.Unmarshal(output, &data); err != nil {
		return nil, fmt.Errorf("ffprobe json: %w", err)
	}
	return data, nil
}

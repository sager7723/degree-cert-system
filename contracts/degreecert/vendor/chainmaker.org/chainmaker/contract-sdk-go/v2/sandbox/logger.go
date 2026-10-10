/*
Copyright (C) BABEC. All rights reserved.
Copyright (C) THL A29 Limited, a Tencent company. All rights reserved.

SPDX-License-Identifier: Apache-2.0
*/

package sandbox

import (
	"os"
	"strings"
	"time"

	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
)

const (
	SHOWLINE        = true
	LEVEL_DEBUG     = "DEBUG"
	LEVEL_INFO      = "INFO"
	LEVEL_WARN      = "WARN"
	LEVEL_ERROR     = "ERROR"
	MODULE_SANDBOX  = "Sandbox"
	MODULE_CONTRACT = "Contract"
)

var (
	contractLoggerModule string
)

func newDockerLogger(name, level string) *zap.SugaredLogger {
	encoder := getEncoder()
	writeSyncer := getLogWriter()

	// default log level is info
	logLevel := new(zapcore.Level)
	if err := logLevel.UnmarshalText([]byte(level)); err != nil {
		panic("unknown log level, logLevelFromConfig: " + level + "," + err.Error())
	}

	core := zapcore.NewCore(
		encoder,
		writeSyncer,
		logLevel,
	)

	logger := zap.New(core).Named(name)
	defer func(logger *zap.Logger) {
		err := logger.Sync()
		if err != nil {
			return
		}
	}(logger)

	if SHOWLINE {
		logger = logger.WithOptions(zap.AddCaller())
	}

	sugarLogger := logger.Sugar()

	return sugarLogger
}

func getEncoder() zapcore.Encoder {

	encoderConfig := zapcore.EncoderConfig{
		TimeKey:        "time",
		LevelKey:       "level",
		NameKey:        "logger",
		CallerKey:      "line",
		MessageKey:     "msg",
		StacktraceKey:  "stacktrace",
		LineEnding:     zapcore.DefaultLineEnding,
		EncodeLevel:    CustomLevelEncoder,
		EncodeTime:     CustomTimeEncoder,
		EncodeDuration: zapcore.SecondsDurationEncoder,
		EncodeCaller:   zapcore.ShortCallerEncoder,
		EncodeName:     zapcore.FullNameEncoder,
	}

	return zapcore.NewConsoleEncoder(encoderConfig)
}

func getLogWriter() zapcore.WriteSyncer {

	syncer := zapcore.AddSync(os.Stdout)

	return syncer
}

func CustomLevelEncoder(level zapcore.Level, enc zapcore.PrimitiveArrayEncoder) {
	enc.AppendString("[" + level.CapitalString() + "]")
}

func CustomTimeEncoder(t time.Time, enc zapcore.PrimitiveArrayEncoder) {
	enc.AppendString(t.Format("2006-01-02 15:04:05.000"))
}

func generateLoggerModuleName(iterms ...string) string {
	itermLen := len(iterms)
	var sb strings.Builder
	sb.WriteString("[")
	for index, iterm := range iterms {
		sb.WriteString(iterm)
		if index+1 < itermLen {
			sb.WriteString(" ")
		}
	}
	sb.WriteString("]")

	return sb.String()
}

/*
Copyright (C) BABEC. All rights reserved.
Copyright (C) THL A29 Limited, a Tencent company. All rights reserved.

SPDX-License-Identifier: Apache-2.0
*/

package sandbox

import (
	"runtime"
)

const (
	stackBufferSize = 1048576
)

func GetAllStackMsg() string {
	var buf [stackBufferSize]byte
	n := runtime.Stack(buf[:], true)
	return string(buf[:n])
}

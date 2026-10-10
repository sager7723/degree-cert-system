/*
Copyright (C) BABEC. All rights reserved.
Copyright (C) THL A29 Limited, a Tencent company. All rights reserved.

SPDX-License-Identifier: Apache-2.0
*/

package sdk

import (
	"chainmaker.org/chainmaker/contract-sdk-go/v2/pb/protogo"
)

const (
	// OK constant - status code less than 400, endorser will endorse it.
	// OK means init or invoke successfully.
	OK = 0

	// ERROR constant - default error value
	ERROR = 1
)

var SuccessResponse = protogo.Response{Status: OK, Payload: []byte("ok")}

// Success ...
func Success(payload []byte) protogo.Response {
	return protogo.Response{
		Status:  OK,
		Payload: payload,
	}
}

// Error ...
func Error(msg string) protogo.Response {
	return protogo.Response{
		Status:  ERROR,
		Message: msg,
	}
}

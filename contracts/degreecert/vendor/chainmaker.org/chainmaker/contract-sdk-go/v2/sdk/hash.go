/*
Copyright (C) THL A29 Limited, a Tencent company. All rights reserved.

SPDX-License-Identifier: Apache-2.0
*/

package sdk

import (
	"chainmaker.org/chainmaker/common/v2/crypto"
)

type Hash struct {
	hashType crypto.HashType
}

type Options func(*Hash)

// WithHashType
func WithHashType(hashType crypto.HashType) Options {
	return func(h *Hash) {
		h.hashType = hashType
	}
}

// getHashType
func getHashType(hashType ...crypto.HashType) crypto.HashType {
	if len(hashType) == 0 {
		return defaultHashType
	}
	return hashType[0]
}

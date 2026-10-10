//go:build crypto
// +build crypto

/*
Copyright (C) THL A29 Limited, a Tencent company. All rights reserved.

SPDX-License-Identifier: Apache-2.0
*/

package sdk

import (
	"chainmaker.org/chainmaker/common/v2/crypto/bulletproofs"
)

var Bulletproofs BulletproofsInstance

// BulletproofsInstance is the interface that wrap the bulletproofs method
type BulletproofsInstance interface {
	// PedersenAddNum Compute a commitment to x + y from a commitment to x without revealing the value x, where y is a scalar
	// commitment: C = xB + rB'
	// value: the value y
	// return1: the new commitment to x + y: C' = (x + y)B + rB'
	PedersenAddNum(commitment []byte, num uint64) ([]byte, error)

	// PedersenAddCommitment Compute a commitment to x + y from commitments to x and y, without revealing the value x and y
	// commitment1: commitment to x: Cx = xB + rB'
	// commitment2: commitment to y: Cy = yB + sB'
	// return: commitment to x + y: C = (x + y)B + (r + s)B'
	PedersenAddCommitment(commitment1, commitment2 []byte) ([]byte, error)

	// PedersenSubNum Compute a commitment to x - y from a commitment to x without revealing the value x, where y is a scalar
	// commitment: C = xB + rB'
	// value: the value y
	// return1: the new commitment to x - y: C' = (x - y)B + rB'
	PedersenSubNum(commitment []byte, num uint64) ([]byte, error)

	// PedersenSubCommitment Compute a commitment to x - y from commitments to x and y, without revealing the value x and y
	// commitment1: commitment to x: Cx = xB + rB'
	// commitment2: commitment to y: Cy = yB + sB'
	// return: commitment to x - y: C = (x - y)B + (r - s)B'
	PedersenSubCommitment(commitment1, commitment2 []byte) ([]byte, error)

	// PedersenMulNum Compute a commitment to x * y from a commitment to x and an integer y, without revealing the value x and y
	// commitment1: commitment to x: Cx = xB + rB'
	// value: integer value y
	// return: commitment to x * y: C = (x * y)B + (r * y)B'
	PedersenMulNum(commitment []byte, num uint64) ([]byte, error)

	// Verify Verify the validity of a proof
	// proof: the zero-knowledge proof proving the number committed in commitment is in the range [0, 2^64)
	// commitment: commitment bindingly hiding the number x
	// return: true on valid proof, false otherwise
	Verify(proof, commitment []byte) (bool, error)
}

type BulletproofsInstanceImpl struct{}

func NewBulletproofsInstance() BulletproofsInstance {
	return &BulletproofsInstanceImpl{}
}

func (*BulletproofsInstanceImpl) PedersenAddNum(commitment []byte, num uint64) ([]byte, error) {
	return bulletproofs.PedersenAddNum(commitment, num)
}

func (*BulletproofsInstanceImpl) PedersenAddCommitment(commitment1, commitment2 []byte) ([]byte, error) {
	return bulletproofs.PedersenAddCommitment(commitment1, commitment2)
}

func (*BulletproofsInstanceImpl) PedersenSubNum(commitment []byte, num uint64) ([]byte, error) {
	return bulletproofs.PedersenSubNum(commitment, num)
}

func (*BulletproofsInstanceImpl) PedersenSubCommitment(commitment1, commitment2 []byte) ([]byte, error) {
	return bulletproofs.PedersenSubCommitment(commitment1, commitment2)
}

func (*BulletproofsInstanceImpl) PedersenMulNum(commitment []byte, num uint64) ([]byte, error) {
	return bulletproofs.PedersenMulNum(commitment, num)
}

func (*BulletproofsInstanceImpl) Verify(proof, commitment []byte) (bool, error) {
	return bulletproofs.Verify(proof, commitment)
}

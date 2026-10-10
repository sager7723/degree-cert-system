//go:build crypto
// +build crypto

/*
Copyright (C) THL A29 Limited, a Tencent company. All rights reserved.

SPDX-License-Identifier: Apache-2.0
*/

package sdk

import (
	"errors"
	"fmt"
	"math/big"
	"strconv"

	"chainmaker.org/chainmaker/common/v2/crypto/paillier"
)

var Paillier PaillierInstance

// PaillierInstance is the interface that wrap the paillier method
type PaillierInstance interface {
	// AddCiphertext homomorphically adds together two cipher texts.
	// To do this we multiply the two cipher texts, upon decryption, the resulting
	// plain text will be the sum of the corresponding plain texts.
	AddCiphertext(pkBytes []byte, ctBytes1 []byte, ctBytes2 []byte) ([]byte, error)

	// AddPlaintext homomorphically adds a passed constant to the encrypted integer
	// (our cipher text). We do this by multiplying the constant with our
	// ciphertext. Upon decryption, the resulting plain text will be the sum of
	// the plaintext integer and the constant.
	AddPlaintext(pkBytes, ctBytes []byte, ptStr string) ([]byte, error)

	// SubCiphertext homomorphically subs two cipher texts.
	// To do this we multiply the two cipher texts, upon decryption, the resulting
	// plain text will be the subs of the corresponding plain texts.
	SubCiphertext(pkBytes, ctBytes1, ctBytes2 []byte) ([]byte, error)

	// SubPlaintext homomorphically subs a passed constant to the encrypted integer
	// (our cipher text). We do this by multiplying the constant with our
	// ciphertext. Upon decryption, the resulting plain text will be the subs of
	// the plaintext integer and the constant.
	SubPlaintext(pkBytes, ctBytes []byte, ptStr string) ([]byte, error)

	// NumMul homomorphically multiplies an encrypted integer (cipher text) by a
	// constant. We do this by raising our cipher text to the power of the passed
	// constant. Upon decryption, the resulting plain text will be the product of
	// the plaintext integer and the constant.
	NumMul(pkBytes, ctBytes []byte, ptStr string) ([]byte, error)
}

type PaillierInstanceImpl struct{}

func NewPaillierInstance() PaillierInstance {
	return &PaillierInstanceImpl{}
}

func (p *PaillierInstanceImpl) AddCiphertext(pkBytes, ctBytes1, ctBytes2 []byte) ([]byte, error) {
	pubKey, err := p.GetPKFromBytes(pkBytes)
	if err != nil {
		return nil, fmt.Errorf("[add ciphertext] unmarshal public key failed, %v", err)
	}
	ct1, err := p.GetCipherFromBytes(ctBytes1)
	if err != nil {
		return nil, fmt.Errorf("[add ciphertext] unmarshal the first cipher bytes failed, %v", err)
	}
	ct2, err := p.GetCipherFromBytes(ctBytes2)
	if err != nil {
		return nil, fmt.Errorf("[add ciphertext] unmarshal the second cipher bytes failed, %v", err)
	}

	result, err := pubKey.AddCiphertext(ct1, ct2)
	if err != nil {
		return nil, fmt.Errorf("[add ciphertext] operate failed, %v", err)
	}
	if result == nil {
		return nil, errors.New("[add ciphertext] operate failed, result is nil")
	}
	return result.Marshal()
}

func (p *PaillierInstanceImpl) AddPlaintext(pkBytes, ctBytes []byte, ptStr string) ([]byte, error) {
	pubKey, err := p.GetPKFromBytes(pkBytes)
	if err != nil {
		return nil, fmt.Errorf("[add ciphertext] unmarshal public key failed, %v", err)
	}
	ct, err := p.GetCipherFromBytes(ctBytes)
	if err != nil {
		return nil, fmt.Errorf("[add plaintext] unmarshal cipher bytes failed, %v", err)
	}

	ptInt64, err := strconv.ParseInt(ptStr, 10, 64)
	if err != nil {
		return nil, fmt.Errorf("[add plaintext] parse int plaintext failed, %v", err)
	}
	pt := new(big.Int).SetInt64(ptInt64)

	result, err := pubKey.AddPlaintext(ct, pt)
	if err != nil {
		return nil, fmt.Errorf("[add plaintext] operate failed, %v", err)
	}
	if result == nil {
		return nil, errors.New("[add plaintext] operate failed, result is nil")
	}

	return result.Marshal()
}

func (p *PaillierInstanceImpl) SubCiphertext(pkBytes, ctBytes1, ctBytes2 []byte) ([]byte, error) {
	pubKey, err := p.GetPKFromBytes(pkBytes)
	if err != nil {
		return nil, fmt.Errorf("[add ciphertext] unmarshal public key failed, %v", err)
	}
	ct1, err := p.GetCipherFromBytes(ctBytes1)
	if err != nil {
		return nil, fmt.Errorf("[sub ciphertext] unmarshal the first cipher bytes failed, %v", err)
	}
	ct2, err := p.GetCipherFromBytes(ctBytes2)
	if err != nil {
		return nil, fmt.Errorf("[sub ciphertext] unmarshal the second cipher bytes failed, %v", err)
	}
	result, err := pubKey.SubCiphertext(ct1, ct2)
	if err != nil {
		return nil, fmt.Errorf("[sub ciphertext] operate failed, %v", err)
	}
	if result == nil {
		return nil, errors.New("[sub ciphertext] operate failed, result is nil")
	}

	return result.Marshal()
}

func (p *PaillierInstanceImpl) SubPlaintext(pkBytes, ctBytes []byte, ptStr string) ([]byte, error) {
	pubKey, err := p.GetPKFromBytes(pkBytes)
	if err != nil {
		return nil, fmt.Errorf("[add ciphertext] unmarshal public key failed, %v", err)
	}
	ct, err := p.GetCipherFromBytes(ctBytes)
	if err != nil {
		return nil, fmt.Errorf("[sub plaintext] unmarshal cipher bytes failed, %v", err)
	}

	ptInt64, err := strconv.ParseInt(ptStr, 10, 64)
	if err != nil {
		return nil, fmt.Errorf("[sub plaintext] parse int plaintext failed, %v", err)
	}
	pt := new(big.Int).SetInt64(ptInt64)

	result, err := pubKey.SubPlaintext(ct, pt)
	if err != nil {
		return nil, fmt.Errorf("[sub plaintext] operate failed, %v", err)
	}
	if result == nil {
		return nil, errors.New("[sub plaintext] operate failed, result is nil")
	}

	return result.Marshal()
}

func (p *PaillierInstanceImpl) NumMul(pkBytes, ctBytes []byte, ptStr string) ([]byte, error) {
	pubKey, err := p.GetPKFromBytes(pkBytes)
	if err != nil {
		return nil, fmt.Errorf("[add ciphertext] unmarshal public key failed, %v", err)
	}
	ct, err := p.GetCipherFromBytes(ctBytes)
	if err != nil {
		return nil, fmt.Errorf("[num mul] unmarshal cipher bytes failed, %v", err)
	}

	ptInt64, err := strconv.ParseInt(ptStr, 10, 64)
	if err != nil {
		return nil, fmt.Errorf("[num mul] parse int plaintext failed, %v", err)
	}
	pt := new(big.Int).SetInt64(ptInt64)

	result, err := pubKey.NumMul(ct, pt)
	if err != nil {
		return nil, fmt.Errorf("[num mul] operate failed, %v", err)
	}
	if result == nil {
		return nil, errors.New("[num mul] operate failed, result is nil")
	}

	return result.Marshal()
}

func (p *PaillierInstanceImpl) GetPKFromBytes(pkBytes []byte) (*paillier.PubKey, error) {
	pubKey := new(paillier.PubKey)
	err := pubKey.Unmarshal(pkBytes)
	if err != nil {
		return nil, err
	}
	return pubKey, nil
}

func (p *PaillierInstanceImpl) GetCipherFromBytes(ctBytes []byte) (*paillier.Ciphertext, error) {
	ct := new(paillier.Ciphertext)
	err := ct.Unmarshal(ctBytes)
	if err != nil {
		return nil, err
	}
	return ct, nil
}

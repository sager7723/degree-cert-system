/*
Copyright (C) THL A29 Limited, a Tencent company. All rights reserved.

SPDX-License-Identifier: Apache-2.0
*/

package sdk

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"hash"
	"strings"

	"chainmaker.org/chainmaker/common/v2/crypto"
	"github.com/tjfoc/gmsm/sm3"
	"golang.org/x/crypto/sha3"
)

const (
	emptyString     = ""
	keysConnector   = ""
	defaultHashType = crypto.HASH_TYPE_SM3
)

// StoreMap is used to store key,value pairs automatically. Every key,value pair in it will be
// stored(use sdk's PutStateByte) when it was added into StoreMap. StoreMap is a structure like solidity's mapping.
// This structure is just for convenient, it's performance is worse than using PutStateByte directly because when you
// create a StoreMap(call NewStoreMap), it will call at least 1 time GetStateByte to find whether the StoreMap exist
// in store and if the StoreMap is not exist in store, it will call 1 time PutStateByte to store the StoreMap.
// If you have many key,value pairs to store, you can use it.
type StoreMap struct {
	// Name store map name
	Name string `json:"name"`
	// Depth store map deep
	Depth int64 `json:"depth"`
	// HashType
	HashType crypto.HashType `json:"hash_type"`
}

// NewStoreMap return a StoreMap object. It includes at least 1 time GetStateByte call.
func NewStoreMap(name string, depth int64, hashType ...crypto.HashType) (*StoreMap, error) {
	var err error
	var storeMapKey string
	var stateByte []byte
	if depth <= 0 {
		return nil, errors.New("depth must be greater than zero")
	}

	if len(name) == 0 {
		return nil, errors.New("name cannot be empty")
	}

	storeMap := &StoreMap{}
	storeMapKey, err = storeMap.getHash([]byte(name), WithHashType(getHashType(hashType...)))
	if err != nil {
		return nil, err
	}

	stateByte, err = Instance.GetStateByte(name+storeMapKey, emptyString)
	if err != nil {
		return nil, err
	}

	if len(stateByte) != 0 {
		if err = json.Unmarshal(stateByte, &storeMap); err != nil {
			return nil, err
		}
		if storeMap.Depth != depth {
			return nil, fmt.Errorf("storemap %s already exist, but depth is not equal", name)
		}
	} else {
		storeMap.Depth = depth
		storeMap.Name = name
		storeMap.HashType = getHashType(hashType...)
		if err = storeMap.save(); err != nil {
			return nil, err
		}
	}

	return storeMap, nil
}

// Get the value of Key in the StoreMap
func (c *StoreMap) Get(key []string) ([]byte, error) {
	if err := c.checkMapDepth(key); err != nil {
		return nil, err
	}

	generateKey, field, err := c.generateKey(key)
	if err != nil {
		return nil, err
	}
	val, err := Instance.GetStateByte(generateKey, field)
	if err != nil {
		return nil, err
	}
	return val, err
}

// Set the value of Key in the StoreMap
func (c *StoreMap) Set(key []string, value []byte) (err error) {
	if err = c.checkMapDepth(key); err != nil {
		return err
	}

	generateKey, field, err := c.generateKey(key)
	if err != nil {
		return err
	}
	if err = Instance.PutStateByte(generateKey, field, value); err != nil {
		return err
	}

	return nil
}

// Del the Key in the StoreMap
func (c *StoreMap) Del(key []string) (err error) {
	if err = c.checkMapDepth(key); err != nil {
		return err
	}

	generateKey, field, err := c.generateKey(key)
	if err != nil {
		return err
	}
	if err = Instance.DelState(generateKey, field); err != nil {
		return err
	}

	return nil
}

// Exist return whether the Key exist in the StoreMap
func (c *StoreMap) Exist(key []string) (ok bool, err error) {
	if err = c.checkMapDepth(key); err != nil {
		return false, err
	}
	generateKey, field, err := c.generateKey(key)
	if err != nil {
		return false, err
	}

	ret, err := Instance.GetStateByte(generateKey, field)
	if err != nil {
		return false, err
	}
	if len(ret) == 0 {
		return false, nil
	}
	return true, nil
}

// NewStoreMapIteratorPrefixWithKey create a iterator(IteratorPrefixWithKey) of the StoreMap
func (c *StoreMap) NewStoreMapIteratorPrefixWithKey(key []string) (ResultSetKV, error) {
	return Instance.NewIteratorPrefixWithKey(c.generateIteratorKey(key))
}

// save the StoreMap info
func (c *StoreMap) save() error {
	storeMapBytes, err := json.Marshal(c)
	if err != nil {
		return err
	}

	storeMapKey, err := c.getHash([]byte(c.Name))
	if err != nil {
		return err
	}

	if err = Instance.PutStateByte(c.Name+storeMapKey, "", storeMapBytes); err != nil {
		return err
	}
	return nil
}

// checkMapDepth
func (c *StoreMap) checkMapDepth(key []string) error {
	for _, k := range key {
		if len(k) == 0 {
			return errors.New("key cannot be empty")
		}
	}
	if len(key) != int(c.Depth) {
		return errors.New("please check keys")
	}
	return nil
}

// generateKey
func (c *StoreMap) generateKey(key []string) (string, string, error) {
	var field string
	var err error
	field, err = c.getHash([]byte(c.Name))
	if err != nil {
		return emptyString, emptyString, err
	}

	for _, k := range key {
		field, err = c.getHash([]byte(field + k))
		if err != nil {
			return emptyString, emptyString, err
		}
	}
	return c.Name + strings.Join(key, keysConnector), field, nil
}

// generateIteratorKey
func (c *StoreMap) generateIteratorKey(key []string) string {
	return c.Name + strings.Join(key, keysConnector)
}

// getHash
func (c *StoreMap) getHash(data []byte, opt ...Options) (string, error) {
	var hf func() hash.Hash
	ht := new(Hash)
	if c.HashType != 0 {
		ht.hashType = c.HashType
	}

	for _, o := range opt {
		o(ht)
	}
	switch ht.hashType {
	case crypto.HASH_TYPE_SM3:
		hf = sm3.New
	case crypto.HASH_TYPE_SHA256:
		hf = sha256.New
	case crypto.HASH_TYPE_SHA3_256:
		hf = sha3.New256
	default:
		return "", fmt.Errorf("unknown hash algorithm")
	}

	f := hf()

	f.Write(data)
	return hex.EncodeToString(f.Sum(nil)), nil
}

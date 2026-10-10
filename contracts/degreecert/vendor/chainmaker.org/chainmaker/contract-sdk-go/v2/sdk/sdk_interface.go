/*
Copyright (C) BABEC. All rights reserved.
Copyright (C) THL A29 Limited, a Tencent company. All rights reserved.

SPDX-License-Identifier: Apache-2.0
*/

package sdk

import (
	"chainmaker.org/chainmaker/common/v2/serialize"
	"chainmaker.org/chainmaker/contract-sdk-go/v2/pb/protogo"
	vmPb "chainmaker.org/chainmaker/pb-go/v2/vm"
)

// Contract user contract common
type Contract interface {
	// InitContract used to deploy and upgrade contract
	InitContract() protogo.Response
	// UpgradeContract used to upgrade contract
	UpgradeContract() protogo.Response
	// InvokeContract used to invoke user contract
	InvokeContract(method string) protogo.Response
}

var Instance SDKInterface

type SDKInterface interface {
	// GetArgs get arg from transaction parameters
	// @return: 参数map
	GetArgs() map[string][]byte
	// GetState get [key, field] from chain
	// @param key: 获取的参数名
	// @param field: 获取的参数名
	// @return1: 获取结果，格式为string，长度为0代表不存在
	// @return2: 获取错误信息
	GetState(key, field string) (string, error)
	// GetStateWithExists get [key, field] from chain
	// @param key: 获取的参数名
	// @param field: 获取的参数名
	// @return1: 获取结果，格式为string
	// @return2: 是否存在，bool
	// @return3: 获取错误信息
	GetStateWithExists(key, field string) (string, bool, error)
	// GetBatchState get [BatchKeys] from chain
	// @param batchKey: 获取的参数名
	// @return1: 获取结果
	// @return2: 获取错误信息
	GetBatchState(batchKeys []*vmPb.BatchKey) ([]*vmPb.BatchKey, error)
	// GetStateByte get [key, field] from chain
	// @param key: 获取的参数名
	// @param field: 获取的参数名
	// @return1: 获取结果，格式为[]byte, 空字节数组和nil均表示不存在
	// @return2: 获取错误信息
	GetStateByte(key, field string) ([]byte, error)
	// GetStateFromKey get [key] from chain
	// @param key: 获取的参数名
	// @return1: 获取结果，格式为string，长度为0代表不存在
	// @return2: 获取错误信息
	GetStateFromKey(key string) (string, error)
	// GetStateFromKeyWithExists get [key] from chain
	// @param key: 获取的参数名
	// @return1: 获取结果，格式为string
	// @return2: 是否存在，bool
	// @return3: 获取错误信息
	GetStateFromKeyWithExists(key string) (string, bool, error)
	// GetStateFromKeyByte get [key] from chain
	// @param key: 获取的参数名
	// @return1: 获取结果，格式为[]byte, 空字节数组和nil均表示不存在
	// @return2: 获取错误信息
	GetStateFromKeyByte(key string) ([]byte, error)
	// PutState put [key, field, value] to chain
	// @param1 key: 参数名
	// @param1 field: 参数名
	// @param2 value: 参数值，类型为string，空字符串代表删除key
	// @return1: 上传参数错误信息
	PutState(key, field string, value string) error
	// PutStateByte put [key, field, value] to chain
	// @param1 key: 参数名
	// @param1 field: 参数名
	// @param2 value: 参数值，类型为[]byte，空字节数组或nil代表删除key
	// @return1: 上传参数错误信息
	PutStateByte(key, field string, value []byte) error
	// PutStateFromKey put [key, value] to chain
	// @param1 key: 参数名
	// @param2 value: 参数值，类型为string，空字符串代表删除key
	// @return1: 上传参数错误信息
	PutStateFromKey(key string, value string) error
	// PutStateFromKeyByte put [key, value] to chain
	// @param1 key: 参数名
	// @param2 value: 参数值，类型为[]byte，空字节数组或nil代表删除key
	// @return1: 上传参数错误信息
	PutStateFromKeyByte(key string, value []byte) error
	// DelState delete [key, field] to chain
	// @param1 key: 删除的参数名
	// @param1 field: 删除的参数名
	// @return1：删除参数的错误信息
	DelState(key, field string) error
	// DelStateFromKey delete [key] to chain
	// @param1 key: 删除的参数名
	// @return1：删除参数的错误信息
	DelStateFromKey(key string) error
	// GetCreatorOrgId get tx creator org id
	// @return1: 合约创建者的组织ID
	// @return2: 获取错误信息
	GetCreatorOrgId() (string, error)
	// GetCreatorRole get tx creator role
	// @return1: 合约创建者的角色
	// @return2: 获取错误信息
	GetCreatorRole() (string, error)
	// GetCreatorPk get tx creator pk
	// @return1: 合约创建者的公钥
	// @return2: 获取错误信息
	GetCreatorPk() (string, error)
	// GetSenderOrgId get tx sender org id
	// @return1: 交易发起者的组织ID
	// @return2: 获取错误信息
	GetSenderOrgId() (string, error)
	// GetSenderRole get tx sender role
	// @return1: 交易发起者的角色
	// @return2: 获取错误信息
	GetSenderRole() (string, error)
	// GetSenderPk get tx sender pk
	// @return1: 交易发起者的公钥
	// @return2: 获取错误信息
	GetSenderPk() (string, error)
	// GetBlockHeight get tx block height
	// @return1: 当前块高度
	// @return2: 获取错误信息
	GetBlockHeight() (int, error)
	// GetTxId get current tx id
	// @return1: 交易ID
	// @return2: 获取错误信息
	GetTxId() (string, error)
	// GetTxInfo get tx info
	// @param txId :合约交易ID
	GetTxInfo(txId string) protogo.Response
	// GetTxTimeStamp get tx timestamp
	// @return1: 交易timestamp
	// @return2: 获取错误信息
	GetTxTimeStamp() (string, error)
	// EmitEvent emit event, you can subscribe to the event using the SDK
	// @param1 topic: 合约事件的主题
	// @param2 data: 合约事件的数据，参数数量不可大于16
	EmitEvent(topic string, data []string)
	// Log record log to chain server
	// @param message: 事情日志的信息
	//Deprecated
	Log(message string)
	// Debugf record log to chain server
	// @param format: 日志格式化模板
	// @param a: 模板参数
	Debugf(format string, a ...interface{})
	// Infof record log to chain server
	// @param format: 日志格式化模板
	// @param a: 模板参数
	Infof(format string, a ...interface{})
	// Warnf record log to chain server
	// @param format: 日志格式化模板
	// @param a: 模板参数
	Warnf(format string, a ...interface{})
	// Errorf record log to chain server
	// @param format: 日志格式化模板
	// @param a: 模板参数
	Errorf(format string, a ...interface{})
	// CallContract invoke another contract and get response
	// @param1: 合约名称
	// @param2: 合约方法
	// @param3: 合约合约参数
	// @return1: 合约结果
	CallContract(contractName, method string, args map[string][]byte) protogo.Response
	// NewIterator range of [startKey, limitKey), front closed back open
	// @param1: 范围查询起始key
	// @param2: 范围查询结束key
	// @return1: 根据起始key生成的迭代器
	// @return2: 获取错误信息
	NewIterator(startKey string, limitKey string) (ResultSetKV, error)
	// NewIteratorWithField range of [key+"#"+startField, key+"#"+limitField), front closed back open
	// @param1: 分别与param2, param3 构成查询起始和结束的key
	// @param2: [param1 + "#" + param2] 来获取查询起始的key
	// @param3: [param1 + "#" + param3] 来获取查询结束的key
	// @return1: 根据起始位置生成的迭代器
	// @return2: 获取错误信息
	NewIteratorWithField(key string, startField string, limitField string) (ResultSetKV, error)
	// NewIteratorPrefixWithKeyField range of [key+"#"+field, key+"#"+field], front closed back closed
	// @param1: [ param1 + "#" +param2 ] 构成前缀范围查询的key
	// @param2: [ param1 + "#" +param2 ] 构成前缀范围查询的key
	// @return1: 根据起始位置生成的迭代器
	// @return2: 获取错误信息
	NewIteratorPrefixWithKeyField(key string, field string) (ResultSetKV, error)
	// NewIteratorPrefixWithKey range of [key, key], front closed back closed
	// @param1: 前缀范围查询起始key
	// @return1: 根据起始位置生成的迭代器
	// @return2: 获取错误信息
	NewIteratorPrefixWithKey(key string) (ResultSetKV, error)
	// NewHistoryKvIterForKey query all historical data of key, field
	// @param1: 查询历史的key
	// @param2: 查询历史的field
	// @return1: 根据key, field 生成的历史迭代器
	// @return2: 获取错误信息
	NewHistoryKvIterForKey(key, field string) (KeyHistoryKvIter, error)
	// GetSenderAddr Get the address of the origin caller address, same with Origin()
	// @return1: origin caller address
	// @return2: 获取错误信息
	// Deprecated
	GetSenderAddr() (string, error)
	// Sender Get the address of the sender address, if the contract is called by another contract, the result will be
	// the caller contract's address.
	// Sender will return system contract address when executing the init or upgrade method (If you need to return the
	// user address, we recommend using Origin method here), because the init and upgrade methods are cross-contract
	// txs (system contract -> common contract).
	// @return1: sender address
	// @return2: 获取错误信息
	Sender() (string, error)
	// Origin Get the address of the tx origin caller address
	// @return1: origin caller address
	// @return2: 获取错误信息
	Origin() (string, error)
	// GetContractName Get the contract name
	// @return1: contract name
	// @return2: 获取错误信息
	GetContractName() (string, error)
	// GetContractAddr Get the contract addr
	// @return1: contract addr
	// @return2: 获取错误信息
	GetContractAddr() (string, error)
}

// ResultSet iterator query result
type ResultSet interface {
	// NextRow get next row,
	// sql: column name is EasyCodec key, value is EasyCodec string val. as: val := ec.getString("columnName")
	// kv iterator: key/value is EasyCodec key for "key"/"value", value type is []byte. as: k, _ := ec.GetString("key") v, _ := ec.GetBytes("value")
	NextRow() (*serialize.EasyCodec, error)
	// HasNext return does the next line exist
	HasNext() bool
	// Close .
	Close() (bool, error)
}

type ResultSetKV interface {
	ResultSet
	// Next return key,field,value,code
	Next() (string, string, []byte, error)
}

type KeyHistoryKvIter interface {
	ResultSet
	// Next return txId, blockHeight, timestamp, value, isDelete, error
	Next() (*KeyModification, error)
}

type KeyModification struct {
	Key         string
	Field       string
	Value       []byte
	TxId        string
	BlockHeight int
	IsDelete    bool
	Timestamp   string
}

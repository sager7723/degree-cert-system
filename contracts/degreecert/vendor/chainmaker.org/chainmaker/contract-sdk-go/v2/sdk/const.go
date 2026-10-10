/*
Copyright (C) BABEC. All rights reserved.
Copyright (C) THL A29 Limited, a Tencent company. All rights reserved.

SPDX-License-Identifier: Apache-2.0
*/

package sdk

const (
	KeyRegisterProcessName = "KEY_REGISTER_PROCESS_NAME"
	KeySenderAddr          = "KEY_SENDER_ADDR"

	KeyCallContractResp = "KEY_CALL_CONTRACT_RESPONSE"
	KeyCallContractReq  = "KEY_CALL_CONTRACT_REQUEST"

	KeyStateKey   = "KEY_STATE_KEY"
	KeyUserKey    = "KEY_USER_KEY"
	KeyUserField  = "KEY_USER_FIELD"
	KeyStateValue = "KEY_STATE_VALUE"

	KeyKVIterKey = "KEY_KV_ITERATOR_KEY"
	KeyIterIndex = "KEY_KV_ITERATOR_INDEX"

	KeyHistoryIterKey   = "KEY_HISTORY_ITERATOR_KEY"
	KeyHistoryIterField = "KEY_HISTORY_ITERATOR_FIELD"
	//KeyHistoryIterIndex = "KEY_HISTORY_ITERATOR_INDEX"

	KeyContractName     = "KEY_CONTRACT_NAME"
	KeyIteratorFuncName = "KEY_ITERATOR_FUNC_NAME"
	KeyIterStartKey     = "KEY_ITERATOR_START_KEY"
	KeyIterStartField   = "KEY_ITERATOR_START_FIELD"
	KeyIterLimitKey     = "KEY_ITERATOR_LIMIT_KEY"
	KeyIterLimitField   = "KEY_ITERATOR_LIMIT_FIELD"
	KeyWriteMap         = "KEY_WRITE_MAP"
	KeyIteratorHasNext  = "KEY_ITERATOR_HAS_NEXT"

	KeyTxId        = "KEY_TX_ID"
	KeyBlockHeight = "KEY_BLOCK_HEIGHT"
	KeyIsDelete    = "KEY_IS_DELETE"
	KeyTimestamp   = "KEY_TIMESTAMP"
)

const (
	MapSize = 8

	// common easyCodec key
	EC_KEY_TYPE_KEY          ECKeyType = "key"
	EC_KEY_TYPE_FIELD        ECKeyType = "field"
	EC_KEY_TYPE_VALUE        ECKeyType = "value"
	EC_KEY_TYPE_TX_ID        ECKeyType = "txId"
	EC_KEY_TYPE_BLOCK_HEITHT ECKeyType = "blockHeight"
	EC_KEY_TYPE_IS_DELETE    ECKeyType = "isDelete"
	EC_KEY_TYPE_TIMESTAMP    ECKeyType = "timestamp"

	// stateKvIterator method
	FuncKvIteratorCreate    = "createKvIterator"
	FuncKvPreIteratorCreate = "createKvPreIterator"
	FuncKvIteratorHasNext   = "kvIteratorHasNext"
	FuncKvIteratorNext      = "kvIteratorNext"
	FuncKvIteratorClose     = "kvIteratorClose"

	// keyHistoryKvIterator method
	FuncKeyHistoryIterHasNext = "keyHistoryIterHasNext"
	FuncKeyHistoryIterNext    = "keyHistoryIterNext"
	FuncKeyHistoryIterClose   = "keyHistoryIterClose"

	// int32 representation of bool
	BoolTrue  Bool = 1
	BoolFalse Bool = 0

	sandboxKVStoreSeparator = "#"

	// default batch keys count limit
	defaultLimitKeys = 10000

	// MaxKeyFieldSumLen limits the length of state key (contract key + # + contract filed) can not exceed 600，
	// consistent with tx_sim_context
	MaxKeyFieldSumLen = 599

	// StateRegex is the regex used to check state keys and fields for contracts
	StateRegex = "^[a-zA-Z0-9._-]+$"
)

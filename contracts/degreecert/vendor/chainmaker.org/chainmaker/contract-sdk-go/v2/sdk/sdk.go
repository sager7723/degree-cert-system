/*
Copyright (C) BABEC. All rights reserved.
Copyright (C) THL A29 Limited, a Tencent company. All rights reserved.

SPDX-License-Identifier: Apache-2.0
*/

package sdk

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"chainmaker.org/chainmaker/common/v2/bytehelper"
	"chainmaker.org/chainmaker/common/v2/serialize"
	"chainmaker.org/chainmaker/contract-sdk-go/v2/pb/protogo"
	"chainmaker.org/chainmaker/pb-go/v2/syscontract"
	vmPb "chainmaker.org/chainmaker/pb-go/v2/vm"
	"chainmaker.org/chainmaker/protocol/v2"
	"github.com/gogo/protobuf/proto"
	"go.uber.org/zap"
)

// check interface implement
var _ SDKInterface = (*SDK)(nil)

type Bool int32

type ECKeyType = string

type SDK struct {
	args map[string][]byte

	crossCtx *protogo.CrossContext
	// sdk向RuntimeClient发送消息的方法
	sendSysCallRequestWithRespNotify func(msg *protogo.DockerVMMessage, responseNotify func(message *protogo.DockerVMMessage))

	// cache
	readMap  map[string][]byte
	writeMap map[string][]byte
	// contract parameters
	creatorOrgId  string
	creatorRole   string
	creatorPk     string
	senderOrgId   string
	senderRole    string
	senderPk      string
	senderAddress string
	blockHeight   string
	txId          string
	originalTxId  string
	chainId       string
	txTimeStamp   string
	// events
	contractName string
	contractAddr string
	events       []*protogo.Event
	// logger
	contractLogger *zap.SugaredLogger
	sandboxLogger  *zap.SugaredLogger
	origin         string
}

func NewSDK(
	crossCtx *protogo.CrossContext,
	sendFunc func(msg *protogo.DockerVMMessage, responseNotify func(msg *protogo.DockerVMMessage)),
	txId string,
	originalTxId string,
	chainId string,
	contractName string,
	contractAddr string,
	contractLogger *zap.SugaredLogger,
	SandboxLogger *zap.SugaredLogger,
	args map[string][]byte,
) *SDK {
	var events []*protogo.Event

	// remove unused key in arguments. It may be used in the other vm types
	// remove __tx_id__
	_ = initStubContractParam(args, protocol.ContractTxIdParam)

	s := &SDK{
		crossCtx:                         crossCtx,
		sendSysCallRequestWithRespNotify: sendFunc,

		txId:           txId,
		originalTxId:   originalTxId,
		chainId:        chainId,
		args:           args,
		readMap:        make(map[string][]byte, MapSize),
		writeMap:       make(map[string][]byte, MapSize),
		creatorOrgId:   initStubContractParam(args, protocol.ContractCreatorOrgIdParam),
		creatorRole:    initStubContractParam(args, protocol.ContractCreatorRoleParam),
		creatorPk:      initStubContractParam(args, protocol.ContractCreatorPkParam),
		senderOrgId:    initStubContractParam(args, protocol.ContractSenderOrgIdParam),
		senderRole:     initStubContractParam(args, protocol.ContractSenderRoleParam),
		senderPk:       initStubContractParam(args, protocol.ContractSenderPkParam),
		senderAddress:  initStubContractParam(args, protocol.ContractCrossCallerParam),
		blockHeight:    initStubContractParam(args, protocol.ContractBlockHeightParam),
		txTimeStamp:    initStubContractParam(args, protocol.ContractTxTimeStamp),
		contractLogger: contractLogger,
		sandboxLogger:  SandboxLogger,
		events:         events,
		contractName:   contractName,
		contractAddr:   contractAddr,
	}

	return s
}

func initStubContractParam(args map[string][]byte, key string) string {
	if value, ok := args[key]; ok {
		delete(args, key)
		return string(value)
	} else {
		//s.sandboxLogger.Errorf("init contract parameter [%v] failed", key)
		return ""
	}
}

func (s *SDK) GetArgs() map[string][]byte {
	return s.args
}

func (s *SDK) GetState(key, field string) (string, error) {
	// get from chain maker
	value, err := s.GetStateByte(key, field)
	if err != nil {
		return "", err
	}
	return string(value), nil
}

func (s *SDK) GetStateWithExists(key, field string) (string, bool, error) {
	// get from chain maker
	value, err := s.GetStateByte(key, field)
	if err != nil {
		return "", false, err
	}
	// if value is nil or empty slice, return not exist
	// it is the same operation as the storage packages
	// and the protobuf will marshal nil to empty slice when send back read map and write map.
	// so the nil value and empty slice are the same here
	if len(value) == 0 {
		return "", false, nil
	}
	return string(value), true, nil
}

func (s *SDK) GetBatchState(batchKeys []*vmPb.BatchKey) ([]*vmPb.BatchKey, error) {
	s.sandboxLogger.Debugf("get batch state for keys: %v", batchKeys)
	var (
		done bool

		writeSetValues    []*vmPb.BatchKey
		readSetValues     []*vmPb.BatchKey
		emptyWriteSetKeys []*vmPb.BatchKey
		emptyReadSetKeys  []*vmPb.BatchKey
	)

	if err := s.batchKeysLimit(batchKeys); err != nil {
		return nil, err
	}

	for _, key := range batchKeys {
		if err := protocol.CheckKeyFieldStr(key.Key, key.Field); err != nil {
			return nil, err
		}
	}

	// batch get from write set
	if writeSetValues, emptyWriteSetKeys, done = s.batchGetFromWriteSet(batchKeys); done {
		return writeSetValues, nil
	}

	// batch get read set
	if readSetValues, emptyReadSetKeys, done = s.batchGetFromReadSet(emptyWriteSetKeys); done {
		return append(writeSetValues, readSetValues...), nil
	}

	// get batch state
	value, err := s.getBatchState(emptyReadSetKeys)
	if err != nil {
		return nil, err
	}

	s.sandboxLogger.Debugf("get batch state finished for batch keys %v, values: %v", batchKeys, value)

	return sortBatchKeyResults(batchKeys, append(append(writeSetValues, readSetValues...), value...))
}

// sortBatchKeyResults order the results of GetBatchState and GetBatchStateWithLock.
// Because the query result of each Key may be have different sources,
// such as local WriteSets, local ReadSets, history WriteSets, history ReadSets, RelyCache,
// DB, and so on, so we cannot guarantee the order of them.
func sortBatchKeyResults(batchKeys []*vmPb.BatchKey, batchKeyResults []*vmPb.BatchKey) ([]*vmPb.BatchKey, error) {
	if len(batchKeys) != len(batchKeyResults) {
		return nil, fmt.Errorf("length of batchKeys [%d] and batchKey results [%d] unequal",
			len(batchKeys), len(batchKeyResults))
	}

	batchKeysMap := make(map[string]*vmPb.BatchKey)
	batchKeysSorted := make([]*vmPb.BatchKey, len(batchKeys))
	for i := 0; i < len(batchKeyResults); i++ {
		batchKeysMap[batchKeyResults[i].GetKey()+"#"+batchKeyResults[i].GetField()] = batchKeyResults[i]
	}

	var ok bool
	for i := 0; i < len(batchKeys); i++ {
		batchKeysSorted[i], ok = batchKeysMap[batchKeys[i].GetKey()+"#"+batchKeys[i].GetField()]
		if !ok {
			return nil, fmt.Errorf("batchKey [%s] was not found", batchKeys[i].GetKey())
		}
	}

	return batchKeysSorted, nil
}

func (s *SDK) GetStateByte(key, field string) ([]byte, error) {
	s.sandboxLogger.Debugf("get state for [%s#%s]", key, field)
	if err := protocol.CheckKeyFieldStr(key, field); err != nil {
		return nil, err
	}

	// get from write set
	if value, done := s.getFromWriteSet(key, field); done {
		return value, nil
	}

	// get from read set
	if value, done := s.getFromReadSet(key, field); done {
		return value, nil
	}

	// get from chain maker
	return s.getState(key, field)
}

func (s *SDK) GetStateFromKey(key string) (string, error) {
	return s.GetState(key, "")
}

func (s *SDK) GetStateFromKeyWithExists(key string) (string, bool, error) {
	return s.GetStateWithExists(key, "")
}

func (s *SDK) GetStateFromKeyByte(key string) ([]byte, error) {
	return s.GetStateByte(key, "")
}

func (s *SDK) getBatchState(batchKeys []*vmPb.BatchKey) ([]*vmPb.BatchKey, error) {
	responseCh := make(chan *protogo.DockerVMMessage, 1)

	//getStateKeys := s.constructBatchKey(batchKeys)
	getBatchStateKeys := vmPb.BatchKeys{Keys: batchKeys}
	getBatchStateKeysByte, err := getBatchStateKeys.Marshal()
	if err != nil {
		return nil, err
	}

	respNotify := func(resp *protogo.DockerVMMessage) {
		responseCh <- resp
	}

	getStateMsg := &protogo.DockerVMMessage{
		ChainId:      s.chainId,
		TxId:         s.txId,
		Type:         protogo.DockerVMType_GET_BATCH_STATE_REQUEST,
		CrossContext: s.crossCtx,
		SysCallMessage: &protogo.SysCallMessage{
			Payload: map[string][]byte{
				KeyContractName: []byte(s.contractName),
				KeyStateKey:     getBatchStateKeysByte,
			},
		},
		Request:  nil,
		Response: nil,
	}
	s.sendSysCallRequestWithRespNotify(getStateMsg, respNotify)

	result := <-responseCh

	if result.SysCallMessage.Code == protocol.ContractSdkSignalResultFail {
		return nil, errors.New(result.SysCallMessage.Message)
	}

	value := result.SysCallMessage.Payload[KeyStateValue]
	keys := &vmPb.BatchKeys{}
	if err = keys.Unmarshal(value); err != nil {
		s.sandboxLogger.Debugf("unmarshal value err is: %v\n", err.Error())
	}
	s.batchPutIntoReadSet(keys.Keys)
	return keys.Keys, nil
}

func (s *SDK) getState(key, field string) ([]byte, error) {
	responseCh := make(chan *protogo.DockerVMMessage, 1)

	getStateKey := protocol.GetKeyStr(key, field)

	respNotify := func(resp *protogo.DockerVMMessage) {
		responseCh <- resp
	}

	getStateMsg := &protogo.DockerVMMessage{
		ChainId:      s.chainId,
		TxId:         s.txId,
		Type:         protogo.DockerVMType_GET_STATE_REQUEST,
		CrossContext: s.crossCtx,
		SysCallMessage: &protogo.SysCallMessage{
			Payload: map[string][]byte{
				KeyContractName: []byte(s.contractName),
				KeyStateKey:     getStateKey,
			},
		},
		Request:  nil,
		Response: nil,
	}
	s.sendSysCallRequestWithRespNotify(getStateMsg, respNotify)

	result := <-responseCh

	if result.SysCallMessage.Code == protocol.ContractSdkSignalResultFail {
		return nil, errors.New(result.SysCallMessage.Message)
	}

	value := result.SysCallMessage.Payload[KeyStateValue]
	s.putIntoReadSet(key, field, value)
	return value, nil
}

func (s *SDK) PutState(key, field string, value string) error {
	if err := checkKeyFieldStr(key, field); err != nil {
		return err
	}

	s.putIntoWriteSet(key, field, []byte(value))
	return nil
}

func (s *SDK) PutStateByte(key, field string, value []byte) error {
	if err := checkKeyFieldStr(key, field); err != nil {
		return err
	}

	s.putIntoWriteSet(key, field, value)
	return nil
}

func (s *SDK) PutStateFromKey(key string, value string) error {
	if err := checkKeyFieldStr(key, ""); err != nil {
		return err
	}

	s.putIntoWriteSet(key, "", []byte(value))
	return nil
}

func (s *SDK) PutStateFromKeyByte(key string, value []byte) error {
	if err := checkKeyFieldStr(key, ""); err != nil {
		return err
	}

	s.putIntoWriteSet(key, "", value)
	return nil
}

func (s *SDK) DelState(key, field string) error {
	if err := checkKeyFieldStr(key, field); err != nil {
		return err
	}

	s.putIntoWriteSet(key, field, nil)
	return nil
}

func (s *SDK) DelStateFromKey(key string) error {
	if err := checkKeyFieldStr(key, ""); err != nil {
		return err
	}

	s.putIntoWriteSet(key, "", nil)
	return nil
}

func (s *SDK) getFromWriteSet(key, field string) ([]byte, bool) {
	contractKey := s.constructKey(key, field)
	s.sandboxLogger.Debugf("get key[%s] from write set\n", contractKey)
	if txWrite, ok := s.writeMap[contractKey]; ok {
		return txWrite, true
	}
	return nil, false
}

func (s *SDK) batchGetFromWriteSet(batchKeys []*vmPb.BatchKey) ([]*vmPb.BatchKey, []*vmPb.BatchKey, bool) {
	txWrites := make([]*vmPb.BatchKey, 0, len(batchKeys))
	emptyTxWritesKeys := make([]*vmPb.BatchKey, 0, len(batchKeys))
	for _, k := range batchKeys {
		contractKey := s.constructKey(k.Key, k.Field)
		s.sandboxLogger.Debugf("batch get key[%s] from write set\n", contractKey)
		txWrite, ok := s.writeMap[contractKey]
		if !ok {
			emptyTxWritesKeys = append(emptyTxWritesKeys, k)
		} else {
			k.Value = txWrite
			txWrites = append(txWrites, k)
		}
	}

	if len(emptyTxWritesKeys) == 0 {
		return txWrites, nil, true
	}

	return txWrites, emptyTxWritesKeys, false
}

func (s *SDK) getFromReadSet(key, field string) ([]byte, bool) {
	contractKey := s.constructKey(key, field)
	s.sandboxLogger.Debugf("get key[%s] from read set\n", contractKey)
	if txRead, ok := s.readMap[contractKey]; ok {
		return txRead, true
	}
	return nil, false
}

func (s *SDK) batchGetFromReadSet(batchKeys []*vmPb.BatchKey) ([]*vmPb.BatchKey, []*vmPb.BatchKey, bool) {
	txReads := make([]*vmPb.BatchKey, 0, len(batchKeys))
	emptyTxReadsKeys := make([]*vmPb.BatchKey, 0, len(batchKeys))
	for _, k := range batchKeys {
		contractKey := s.constructKey(k.Key, k.Field)
		s.sandboxLogger.Debugf("batch get key[%s] from read set\n", contractKey)
		txRead, ok := s.readMap[contractKey]
		if !ok {
			k.ContractName = s.contractName
			emptyTxReadsKeys = append(emptyTxReadsKeys, k)
		} else {
			k.Value = txRead
			txReads = append(txReads, k)
		}
	}

	if len(emptyTxReadsKeys) == 0 {
		return txReads, nil, true
	}

	return txReads, emptyTxReadsKeys, false
}

func (s *SDK) putIntoWriteSet(key, field string, value []byte) {
	contractKey := s.constructKey(key, field)
	s.writeMap[contractKey] = value
	s.sandboxLogger.Debugf("put key[%s] - value[%s] into write set\n", contractKey, fmt.Sprintf("%.100q", string(value)))
}

func (s *SDK) putIntoReadSet(key, field string, value []byte) {
	contractKey := s.constructKey(key, field)
	s.readMap[contractKey] = value
	s.sandboxLogger.Debugf("put key[%s] - value[%s] into read set\n", key, fmt.Sprintf("%.100q", string(value)))
}

func (s *SDK) batchPutIntoReadSet(batchKeys []*vmPb.BatchKey) {
	for _, batchKey := range batchKeys {
		contractKey := s.constructKey(batchKey.Key, batchKey.Field)
		s.readMap[contractKey] = batchKey.Value
		s.sandboxLogger.Debugf("batch put key[%s] - value[%s] into read set\n", batchKey.Key, batchKey.Value)
	}
}

func (s *SDK) convertByteSliceToStringSlice(value [][]byte) []string {
	stringSlice := make([]string, len(value))
	for _, bytes := range value {
		stringSlice = append(stringSlice, string(bytes))
	}
	return stringSlice
}

func (s *SDK) constructKey(key, field string) string {
	var builder strings.Builder
	builder.WriteString(s.contractName)
	builder.WriteString(sandboxKVStoreSeparator)
	builder.WriteString(key)

	if len(field) > 0 {
		builder.WriteString(sandboxKVStoreSeparator)
		builder.WriteString(field)
	}

	return builder.String()
}

func (s *SDK) batchKeysLimit(keys []*vmPb.BatchKey) error {
	if len(keys) > defaultLimitKeys {
		return fmt.Errorf("over batch keys count limit %d", defaultLimitKeys)
	}
	return nil
}

func (s *SDK) GetWriteMap() map[string][]byte {
	return s.writeMap
}

func (s *SDK) GetReadMap() map[string][]byte {
	return s.readMap
}

func (s *SDK) GetCreatorOrgId() (string, error) {
	if len(s.creatorOrgId) == 0 {
		return s.creatorOrgId, fmt.Errorf("can not get creator org id")
	} else {
		return s.creatorOrgId, nil
	}
}

func (s *SDK) GetCreatorRole() (string, error) {
	return s.creatorRole, nil
}

func (s *SDK) GetCreatorPk() (string, error) {
	if len(s.creatorPk) == 0 {
		return s.creatorPk, fmt.Errorf("can not get creator pk")
	} else {
		return s.creatorPk, nil
	}
}

func (s *SDK) GetSenderOrgId() (string, error) {
	if len(s.senderOrgId) == 0 {
		return s.senderOrgId, fmt.Errorf("can not get sender org id")
	} else {
		return s.senderOrgId, nil
	}
}

func (s *SDK) GetSenderRole() (string, error) {
	return s.senderRole, nil
}

func (s *SDK) GetSenderPk() (string, error) {
	if len(s.senderPk) == 0 {
		return s.senderPk, fmt.Errorf("can not get sender pk")
	} else {
		return s.senderPk, nil
	}
}

func (s *SDK) GetBlockHeight() (int, error) {
	if len(s.blockHeight) == 0 {
		return 0, fmt.Errorf("can not get block height")
	}
	if res, err := strconv.Atoi(s.blockHeight); err != nil {
		return 0, fmt.Errorf("block height [%v] can not convert to type int", s.blockHeight)
	} else {
		return res, nil
	}
}

func (s *SDK) GetTxId() (string, error) {
	if len(s.originalTxId) == 0 {
		return "", fmt.Errorf("can not get tx id")
	} else {
		return s.originalTxId, nil
	}
}

func (s *SDK) GetTxInfo(txId string) protogo.Response {
	paramTxId := "txId"
	paramMethod := "method"

	contractName := syscontract.SystemContract_CHAIN_QUERY.String()
	method := syscontract.ChainQueryFunction_GET_TX_BY_TX_ID.String()
	args := map[string][]byte{
		paramTxId:   []byte(txId),
		paramMethod: []byte(method),
	}

	response := s.CallContract(contractName, method, args)
	return response
}

func (s *SDK) GetTxTimeStamp() (string, error) {
	if len(s.txTimeStamp) == 0 {
		return s.txTimeStamp, fmt.Errorf("can not get tx timestamp")
	}

	return s.txTimeStamp, nil
}

func (s *SDK) EmitEvent(topic string, data []string) {
	newEvent := &protogo.Event{
		Topic:        topic,
		ContractName: s.contractName,
		Data:         data,
	}
	s.events = append(s.events, newEvent)
}

func (s *SDK) GetEvents() []*protogo.Event {
	return s.events
}

func (s *SDK) Log(message string) {
	s.contractLogger.Debugf(message)
}

func (s *SDK) Debugf(format string, a ...interface{}) {
	s.contractLogger.Debugf(format, a...)
}

func (s *SDK) Infof(format string, a ...interface{}) {
	s.contractLogger.Infof(format, a...)
}

func (s *SDK) Warnf(format string, a ...interface{}) {
	s.contractLogger.Warnf(format, a...)
}

func (s *SDK) Errorf(format string, a ...interface{}) {
	s.contractLogger.Errorf(format, a...)
}

func (s *SDK) CallContract(contractName, method string, args map[string][]byte) protogo.Response {
	responseCh := make(chan *protogo.DockerVMMessage)
	respNotify := func(msg *protogo.DockerVMMessage) {
		responseCh <- msg
	}

	// get contract result from runtime server
	// ContractParamSenderAddress is set at the chain
	initialArgs := map[string][]byte{
		protocol.ContractCreatorOrgIdParam: []byte(s.creatorOrgId),
		protocol.ContractCreatorRoleParam:  []byte(s.creatorRole),
		protocol.ContractCreatorPkParam:    []byte(s.creatorPk),
		protocol.ContractSenderOrgIdParam:  []byte(s.senderOrgId),
		protocol.ContractSenderRoleParam:   []byte(s.senderRole),
		protocol.ContractSenderPkParam:     []byte(s.senderPk),
		protocol.ContractBlockHeightParam:  []byte(s.blockHeight),
		protocol.ContractTxTimeStamp:       []byte(s.txTimeStamp),
	}

	// add user defined args
	for key, value := range args {
		initialArgs[key] = value
	}

	callContractPayloadStruct := &protogo.CallContractRequest{
		ContractName:   contractName,
		ContractMethod: method,
		Args:           initialArgs,
	}

	callContractPayload, _ := proto.Marshal(callContractPayloadStruct)

	callContractReq := &protogo.DockerVMMessage{
		ChainId:      s.chainId,
		TxId:         s.txId,
		Type:         protogo.DockerVMType_CALL_CONTRACT_REQUEST,
		CrossContext: s.crossCtx, // TODO
		SysCallMessage: &protogo.SysCallMessage{
			Payload: map[string][]byte{
				KeyCallContractReq: callContractPayload,
			},
		},
		Request:  nil,
		Response: nil,
	}
	s.sendSysCallRequestWithRespNotify(callContractReq, respNotify)

	result := <-responseCh
	if result.SysCallMessage.Code == protogo.DockerVMCode_FAIL {
		return protogo.Response{
			Status:  ERROR,
			Message: result.SysCallMessage.Message,
			Payload: result.SysCallMessage.Payload[KeyCallContractResp],
		}
	}

	callContractResponsePayload := result.SysCallMessage.Payload[KeyCallContractResp]

	var contractResponse protogo.ContractResponse
	_ = proto.Unmarshal(callContractResponsePayload, &contractResponse)

	// merge read write map
	// e.g. contract A -> contract B -> contract A
	// layer3 contract A will merge latest rwset into layer1 contract A
	for key, value := range contractResponse.ReadMap {
		s.readMap[key] = value
	}
	for key, value := range contractResponse.WriteMap {
		s.writeMap[key] = value
	}

	// merge events
	s.events = append(s.events, contractResponse.Events...)

	// return result
	return *contractResponse.Response
}

func (s *SDK) NewIterator(startKey string, limitKey string) (ResultSetKV, error) {
	return s.newIterator(FuncKvIteratorCreate, startKey, "", limitKey, "")
}

func (s *SDK) NewIteratorWithField(key string, startField string, limitField string) (ResultSetKV, error) {
	return s.newIterator(FuncKvIteratorCreate, key, startField, key, limitField)
}

func (s *SDK) NewIteratorPrefixWithKeyField(startKey string, startField string) (ResultSetKV, error) {
	return s.newIterator(FuncKvPreIteratorCreate, startKey, startField, "", "")
}

func (s *SDK) NewIteratorPrefixWithKey(key string) (ResultSetKV, error) {
	return s.newIterator(FuncKvPreIteratorCreate, key, "", "", "")
}

func (s *SDK) newIterator(iteratorFuncName, startKey string, startField string, limitKey string, limitField string) (
	ResultSetKV, error) {
	if err := protocol.CheckKeyFieldStr(startKey, startField); err != nil {
		return nil, err
	}
	if iteratorFuncName == FuncKvIteratorCreate {
		if err := protocol.CheckKeyFieldStr(limitKey, limitField); err != nil {
			return nil, err
		}
	}

	responseCh := make(chan *protogo.DockerVMMessage, 1)
	respNotify := func(msg *protogo.DockerVMMessage) {
		responseCh <- msg
	}

	writeMap := s.GetWriteMap()
	wMBytes, err := json.Marshal(writeMap)
	if err != nil {
		return nil, err
	}

	createKvIteratorParams := map[string][]byte{
		KeyContractName:     []byte(s.contractName),
		KeyIteratorFuncName: []byte(iteratorFuncName),
		KeyIterStartKey:     []byte(startKey),
		KeyIterStartField:   []byte(startField),
		KeyIterLimitKey:     []byte(limitKey),
		KeyIterLimitField:   []byte(limitField),
		KeyWriteMap:         wMBytes,
	}

	// reset writeMap
	//s.writeMap = make(map[string][]byte, MapSize)

	createKvIteratorReq := &protogo.DockerVMMessage{
		ChainId:      s.chainId,
		TxId:         s.txId,
		Type:         protogo.DockerVMType_CREATE_KV_ITERATOR_REQUEST,
		CrossContext: s.crossCtx,
		SysCallMessage: &protogo.SysCallMessage{
			Payload: createKvIteratorParams,
		},
		Request:  nil,
		Response: nil,
	}
	s.sendSysCallRequestWithRespNotify(createKvIteratorReq, respNotify)

	result := <-responseCh

	if result.SysCallMessage.Code == protocol.ContractSdkSignalResultFail {
		return nil, errors.New(result.SysCallMessage.Message)
	}

	value := result.SysCallMessage.Payload[KeyIterIndex]

	index, err := bytehelper.BytesToInt(value)
	if err != nil {
		return nil, fmt.Errorf("get iterator index failed, %s", err.Error())
	}

	return &ResultSetKvImpl{s: s, index: index}, nil
}

func (s *SDK) NewHistoryKvIterForKey(key, field string) (KeyHistoryKvIter, error) {
	if err := protocol.CheckKeyFieldStr(key, field); err != nil {
		return nil, err
	}

	responseCh := make(chan *protogo.DockerVMMessage, 1)
	respNotify := func(msg *protogo.DockerVMMessage) {
		responseCh <- msg
	}

	writeMap := s.GetWriteMap()
	wMapBytes, err := json.Marshal(writeMap)
	if err != nil {
		return nil, err
	}

	createHistoryKVIterParams := map[string][]byte{
		KeyContractName:     []byte(s.contractName),
		KeyHistoryIterKey:   []byte(key),
		KeyHistoryIterField: []byte(field),
		KeyWriteMap:         wMapBytes,
	}

	// reset writeMap
	//s.writeMap = make(map[string][]byte, MapSize)

	createKeyHistoryKvIterReq := &protogo.DockerVMMessage{
		ChainId:      s.chainId,
		TxId:         s.txId,
		Type:         protogo.DockerVMType_CREATE_KEY_HISTORY_ITER_REQUEST,
		CrossContext: s.crossCtx,
		SysCallMessage: &protogo.SysCallMessage{
			Payload: createHistoryKVIterParams,
		},
		Response: nil,
		Request:  nil,
	}

	s.sendSysCallRequestWithRespNotify(createKeyHistoryKvIterReq, respNotify)

	result := <-responseCh

	if result.SysCallMessage.Code == protocol.ContractSdkSignalResultFail {
		return nil, errors.New(result.SysCallMessage.Message)
	}

	value := result.SysCallMessage.Payload[KeyIterIndex]

	index, err := bytehelper.BytesToInt(value)
	if err != nil {
		return nil, fmt.Errorf("get history iterator index failed, %s", err.Error())
	}

	return &KeyHistoryKvIterImpl{
		key:   key,
		field: field,
		index: index,
		s:     s,
	}, nil
}

func (s *SDK) GetSenderAddr() (string, error) {
	return s.Origin()
}

func (s *SDK) Sender() (string, error) {
	if s.crossCtx.CurrentDepth > 0 {
		if len(s.senderAddress) == 0 {
			return s.senderAddress, fmt.Errorf("can not get sender")
		} else {
			return s.senderAddress, nil
		}
	}
	return s.Origin()
}

func (s *SDK) Origin() (string, error) {

	if s.origin != "" {
		return s.origin, nil
	}

	responseCh := make(chan *protogo.DockerVMMessage, 1)
	respNotify := func(msg *protogo.DockerVMMessage) {
		responseCh <- msg
	}

	getSenderAddrReq := &protogo.DockerVMMessage{
		ChainId:        s.chainId,
		TxId:           s.txId,
		Type:           protogo.DockerVMType_GET_SENDER_ADDRESS_REQUEST,
		CrossContext:   s.crossCtx,
		SysCallMessage: nil,
		Response:       nil,
		Request:        nil,
	}

	s.sendSysCallRequestWithRespNotify(getSenderAddrReq, respNotify)

	result := <-responseCh

	if result.SysCallMessage.Code == protocol.ContractSdkSignalResultFail {
		return "", errors.New(result.SysCallMessage.Message)
	}

	origin := string(result.SysCallMessage.Payload[KeySenderAddr])
	s.origin = origin

	return origin, nil
}

// GetContractName Get the contract name
// @return1: contract name
// @return2: 获取错误信息
func (s *SDK) GetContractName() (string, error) {
	return s.contractName, nil

}

// GetContractAddr Get the contract addr
// @return1: contract addr
// @return2: 获取错误信息
func (s *SDK) GetContractAddr() (string, error) {
	if len(s.contractAddr) == 0 {
		return "", fmt.Errorf("can not get contract addr")
	}
	return s.contractAddr, nil
}

// ResultSetKvImpl iterator query result KVdb
type ResultSetKvImpl struct {
	s *SDK

	index int32
}

func (r *ResultSetKvImpl) HasNext() bool {
	responseCh := make(chan *protogo.DockerVMMessage, 1)
	respNotify := func(msg *protogo.DockerVMMessage) {
		responseCh <- msg
	}

	params := map[string][]byte{
		KeyIteratorFuncName: []byte(FuncKvIteratorHasNext),
		KeyIterIndex:        bytehelper.IntToBytes(r.index),
	}

	consumeKvIteratorReq := &protogo.DockerVMMessage{
		ChainId:      r.s.chainId,
		TxId:         r.s.txId,
		Type:         protogo.DockerVMType_CONSUME_KV_ITERATOR_REQUEST,
		CrossContext: r.s.crossCtx,
		SysCallMessage: &protogo.SysCallMessage{
			Payload: params,
		},
		Response: nil,
		Request:  nil,
	}

	r.s.sendSysCallRequestWithRespNotify(consumeKvIteratorReq, respNotify)

	result := <-responseCh

	if result.SysCallMessage.Code == protocol.ContractSdkSignalResultFail {
		return false
	}

	has, err := bytehelper.BytesToInt(result.SysCallMessage.Payload[KeyIteratorHasNext])
	if err != nil {
		return false
	}

	if has == 0 {
		return false
	}

	return true
}

func (r *ResultSetKvImpl) Close() (bool, error) {
	responseCh := make(chan *protogo.DockerVMMessage, 1)
	respNotify := func(msg *protogo.DockerVMMessage) {
		responseCh <- msg
	}

	params := map[string][]byte{
		KeyIteratorFuncName: []byte(FuncKvIteratorClose),
		KeyIterIndex:        bytehelper.IntToBytes(r.index),
	}

	consumeKvIteratorReq := &protogo.DockerVMMessage{
		ChainId:      r.s.chainId,
		TxId:         r.s.txId,
		Type:         protogo.DockerVMType_CONSUME_KV_ITERATOR_REQUEST,
		CrossContext: r.s.crossCtx,
		SysCallMessage: &protogo.SysCallMessage{
			Payload: params,
		},
		Response: nil,
		Request:  nil,
	}

	r.s.sendSysCallRequestWithRespNotify(consumeKvIteratorReq, respNotify)

	result := <-responseCh

	if result.SysCallMessage.Code == protocol.ContractSdkSignalResultFail {
		return false, errors.New(result.SysCallMessage.Message)
	} else if result.SysCallMessage.Code == protocol.ContractSdkSignalResultSuccess {
		return true, nil
	}

	return true, nil
}

func (r *ResultSetKvImpl) NextRow() (*serialize.EasyCodec, error) {
	responseCh := make(chan *protogo.DockerVMMessage, 1)
	respNotify := func(msg *protogo.DockerVMMessage) {
		responseCh <- msg
	}

	params := map[string][]byte{
		KeyIteratorFuncName: []byte(FuncKvIteratorNext),
		KeyIterIndex:        bytehelper.IntToBytes(r.index),
	}

	consumeKvIteratorReq := &protogo.DockerVMMessage{
		ChainId:      r.s.chainId,
		TxId:         r.s.txId,
		Type:         protogo.DockerVMType_CONSUME_KV_ITERATOR_REQUEST,
		CrossContext: r.s.crossCtx,
		SysCallMessage: &protogo.SysCallMessage{
			Payload: params,
		},
		Response: nil,
		Request:  nil,
	}

	r.s.sendSysCallRequestWithRespNotify(consumeKvIteratorReq, respNotify)

	result := <-responseCh

	if result.SysCallMessage.Code == protocol.ContractSdkSignalResultFail {
		return nil, errors.New(result.SysCallMessage.Message)
	}

	key := result.SysCallMessage.Payload[KeyUserKey]
	field := result.SysCallMessage.Payload[KeyUserField]
	value := result.SysCallMessage.Payload[KeyStateValue]

	ec := serialize.NewEasyCodec()
	ec.AddString(EC_KEY_TYPE_KEY, string(key))
	ec.AddString(EC_KEY_TYPE_FIELD, string(field))
	ec.AddBytes(EC_KEY_TYPE_VALUE, value)

	return ec, nil
}

func (r *ResultSetKvImpl) Next() (string, string, []byte, error) {
	ec, err := r.NextRow()
	if err != nil {
		return "", "", nil, err
	}
	key, _ := ec.GetString(EC_KEY_TYPE_KEY)
	field, _ := ec.GetString(EC_KEY_TYPE_FIELD)
	v, _ := ec.GetBytes(EC_KEY_TYPE_VALUE)

	return key, field, v, nil
}

type KeyHistoryKvIterImpl struct {
	s *SDK

	key   string
	field string
	index int32
}

func (k *KeyHistoryKvIterImpl) HasNext() bool {
	responseCh := make(chan *protogo.DockerVMMessage, 1)
	respNotify := func(msg *protogo.DockerVMMessage) {
		responseCh <- msg
	}

	params := map[string][]byte{
		KeyIteratorFuncName: []byte(FuncKeyHistoryIterHasNext),
		KeyIterIndex:        bytehelper.IntToBytes(k.index),
	}

	consumeKvIteratorReq := &protogo.DockerVMMessage{
		ChainId:      k.s.chainId,
		TxId:         k.s.txId,
		Type:         protogo.DockerVMType_CONSUME_KEY_HISTORY_ITER_REQUEST,
		CrossContext: k.s.crossCtx,
		SysCallMessage: &protogo.SysCallMessage{
			Payload: params,
		},
		Response: nil,
		Request:  nil,
	}

	k.s.sendSysCallRequestWithRespNotify(consumeKvIteratorReq, respNotify)

	result := <-responseCh

	if result.SysCallMessage.Code == protocol.ContractSdkSignalResultFail {
		return false
	}

	has, err := bytehelper.BytesToInt(result.SysCallMessage.Payload[KeyIteratorHasNext])
	if err != nil {
		return false
	}

	if Bool(has) == BoolFalse {
		return false
	}

	return true
}

func (k *KeyHistoryKvIterImpl) NextRow() (*serialize.EasyCodec, error) {
	responseCh := make(chan *protogo.DockerVMMessage, 1)
	respNotify := func(msg *protogo.DockerVMMessage) {
		responseCh <- msg
	}

	params := map[string][]byte{
		KeyIteratorFuncName: []byte(FuncKeyHistoryIterNext),
		KeyIterIndex:        bytehelper.IntToBytes(k.index),
	}

	consumeKvIteratorReq := &protogo.DockerVMMessage{
		ChainId:      k.s.chainId,
		TxId:         k.s.txId,
		Type:         protogo.DockerVMType_CONSUME_KEY_HISTORY_ITER_REQUEST,
		CrossContext: k.s.crossCtx,
		SysCallMessage: &protogo.SysCallMessage{
			Payload: params,
		},
		Response: nil,
		Request:  nil,
	}
	k.s.sendSysCallRequestWithRespNotify(consumeKvIteratorReq, respNotify)

	result := <-responseCh

	if result.SysCallMessage.Code == protocol.ContractSdkSignalResultFail {
		return nil, errors.New(result.SysCallMessage.Message)
	}

	/*
		| index | desc        |
		| ---   | ---         |
		| 0     | txId        |
		| 1     | blockHeight |
		| 2     | value       |
		| 3     | isDelete    |
		| 4     | timestamp   |
	*/

	txId := result.SysCallMessage.Payload[KeyTxId]
	blockHeightBytes := result.SysCallMessage.Payload[KeyBlockHeight]
	value := result.SysCallMessage.Payload[KeyStateValue]
	isDeleteBytes := result.SysCallMessage.Payload[KeyIsDelete]
	timestamp := result.SysCallMessage.Payload[KeyTimestamp]

	ec := serialize.NewEasyCodec()
	ec.AddBytes(EC_KEY_TYPE_VALUE, value)
	ec.AddString(EC_KEY_TYPE_TX_ID, string(txId))

	blockHeight, err := bytehelper.BytesToInt(blockHeightBytes)
	if err != nil {
		return nil, err
	}
	ec.AddInt32(EC_KEY_TYPE_BLOCK_HEITHT, blockHeight)

	ec.AddString(EC_KEY_TYPE_TIMESTAMP, string(timestamp))

	isDelete, err := bytehelper.BytesToInt(isDeleteBytes)
	if err != nil {
		return nil, err
	}
	ec.AddInt32(EC_KEY_TYPE_IS_DELETE, isDelete)

	ec.AddString(EC_KEY_TYPE_KEY, k.key)
	ec.AddString(EC_KEY_TYPE_FIELD, k.field)

	return ec, nil
}

func (k *KeyHistoryKvIterImpl) Close() (bool, error) {
	responseCh := make(chan *protogo.DockerVMMessage, 1)
	respNotify := func(msg *protogo.DockerVMMessage) {
		responseCh <- msg
	}

	params := map[string][]byte{
		KeyIteratorFuncName: []byte(FuncKeyHistoryIterClose),
		KeyIterIndex:        bytehelper.IntToBytes(k.index),
	}

	consumeKvIteratorReq := &protogo.DockerVMMessage{
		ChainId:      k.s.chainId,
		TxId:         k.s.txId,
		Type:         protogo.DockerVMType_CONSUME_KEY_HISTORY_ITER_REQUEST,
		CrossContext: k.s.crossCtx,
		SysCallMessage: &protogo.SysCallMessage{
			Payload: params,
		},
		Response: nil,
		Request:  nil,
	}

	k.s.sendSysCallRequestWithRespNotify(consumeKvIteratorReq, respNotify)

	result := <-responseCh

	if result.SysCallMessage.Code == protocol.ContractSdkSignalResultFail {
		return false, errors.New(result.SysCallMessage.Message)
	} else if result.SysCallMessage.Code == protocol.ContractSdkSignalResultSuccess {
		return true, nil
	}

	return true, nil
}

func (k *KeyHistoryKvIterImpl) Next() (*KeyModification, error) {
	ec, err := k.NextRow()
	if err != nil {
		return nil, err
	}

	value, _ := ec.GetBytes(EC_KEY_TYPE_VALUE)
	txId, _ := ec.GetString(EC_KEY_TYPE_TX_ID)
	blockHeight, _ := ec.GetInt32(EC_KEY_TYPE_BLOCK_HEITHT)
	isDeleteBool, _ := ec.GetInt32(EC_KEY_TYPE_IS_DELETE)
	isDelete := false
	if Bool(isDeleteBool) == BoolTrue {
		isDelete = true
	}

	timestamp, _ := ec.GetString(EC_KEY_TYPE_TIMESTAMP)

	return &KeyModification{
		Key:         k.key,
		Field:       k.field,
		Value:       value,
		TxId:        txId,
		BlockHeight: int(blockHeight),
		IsDelete:    isDelete,
		Timestamp:   timestamp,
	}, nil
}

func checkKeyFieldStr(key, field string) error {
	// check sum length of key and filed
	if len(key)+len(field) > MaxKeyFieldSumLen {
		return fmt.Errorf("sum length of key[%s] and field[%s] too long", key, field)
	}

	// check key
	match, err := regexp.MatchString(StateRegex, key)
	if err != nil || !match {
		return fmt.Errorf("key[%s] can only consist of numbers, dot, letters and underscores", key)
	}

	// check field
	if len(field) == 0 {
		return nil
	}
	match, err = regexp.MatchString(StateRegex, field)
	if err != nil || !match {
		return fmt.Errorf("key field[%s] can only consist of numbers, dot, letters and underscores", field)
	}
	return nil
}

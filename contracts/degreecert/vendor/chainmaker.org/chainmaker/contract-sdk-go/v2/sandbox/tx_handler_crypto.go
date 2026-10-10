//go:build crypto
// +build crypto

/*
Copyright (C) BABEC. All rights reserved.
Copyright (C) THL A29 Limited, a Tencent company. All rights reserved.

SPDX-License-Identifier: Apache-2.0
*/

package sandbox

import (
	"strings"
	"time"

	"chainmaker.org/chainmaker/contract-sdk-go/v2/pb/protogo"
	"chainmaker.org/chainmaker/contract-sdk-go/v2/sdk"
	"go.uber.org/zap"
)

const (
	_initContract    = "init_contract"
	_upgradeContract = "upgrade"
	_invokeContract  = "invoke_contract" //Compatible with version < 2.3.0
)

type TxHandler struct {
	sandboxLogger  *zap.SugaredLogger
	contractLogger *zap.SugaredLogger
	contract       sdk.Contract
	contractName   string
	contractAddr   string
	processName    string
	txId           string
	originalTxId   string
	crossCtx       *protogo.CrossContext
	chainId        string

	sendSyscallMsg        func(msg *protogo.DockerVMMessage, responseNotify func(msg *protogo.DockerVMMessage))
	txFinishMsgNotifyFunc func(signal *protogo.DockerVMMessage)
	pendingQueue          chan *protogo.DockerVMMessage
}

func newTxHandler(contract sdk.Contract, processName, contractName, contractAddr string, logger *zap.SugaredLogger) *TxHandler {
	return &TxHandler{
		sandboxLogger:  logger,
		contractLogger: newDockerLogger(contractLoggerModule, config.LogLevel),
		contract:       contract,
		contractName:   contractName,
		contractAddr:   contractAddr,
		processName:    processName,
		pendingQueue:   make(chan *protogo.DockerVMMessage, 200),
	}
}

// PutMsg put txRequest to pendingQueue
func (h *TxHandler) PutMsg(msg *protogo.DockerVMMessage) {
	h.sandboxLogger.Debugf("[%s] put tx request to tx chan, chan len: [%d]", msg.TxId, len(h.pendingQueue))
	//EnterNextStep(msg, protogo.StepType_SANDBOX_GRPC_SEND_TX_REQUEST, "")
	h.pendingQueue <- msg
}

// RegisterTxFinishMsgNotifyFunc register func to send tx finish msg to engine
func (h *TxHandler) RegisterTxFinishMsgNotifyFunc(txFinishMsgNotifyFunc func(msg *protogo.DockerVMMessage)) {
	h.txFinishMsgNotifyFunc = txFinishMsgNotifyFunc
}

// PutMsgWithNotify put tx request to handler and register finish signal callback func
func (h *TxHandler) PutMsgWithNotify(msg *protogo.DockerVMMessage,
	txFinishMsgNotifyFunc func(msg *protogo.DockerVMMessage)) {
	h.RegisterTxFinishMsgNotifyFunc(txFinishMsgNotifyFunc)
	h.PutMsg(msg)
}

// RegisterSyscallMsgSendFunc register runtime client syscall func with call back func
func (h *TxHandler) RegisterSyscallMsgSendFunc(f func(msg *protogo.DockerVMMessage,
	syscallResponseNotifyFunc func(msg *protogo.DockerVMMessage))) {
	h.sendSyscallMsg = f
}

func (h *TxHandler) sendResponse(resp *protogo.DockerVMMessage) {
	h.sendSyscallMsg(resp, nil)
}

func (h *TxHandler) Start() error {
	h.sandboxLogger.Debugf("*** listen tx chan started ***")
	return h.listenPendingQueue()
}

func (h *TxHandler) listenPendingQueue() error {
	currentStatus = BeforeReceive
	errCh := make(chan error)
	for {
		select {
		case msg := <-h.pendingQueue:
			h.sandboxLogger.Debugf("[%s] get msg from tx chan", msg.TxId)
			if err := h.handleTxRequest(msg); err != nil {
				errCh <- err
			}
		case err := <-errCh:
			return err
		}
	}
}

func getOriginalTxId(txId string) string {
	return strings.Split(txId, "#")[0]
}

func (h *TxHandler) handleTxRequest(msg *protogo.DockerVMMessage) error {
	h.chainId = msg.ChainId
	h.txId = msg.TxId
	h.originalTxId = getOriginalTxId(h.txId)
	h.crossCtx = msg.CrossContext
	h.crossCtx.ProcessName = h.processName
	// init time statistics
	startTime := time.Now()
	currentTxDuration.Reset(msg, startTime.UnixNano())
	//EnterNextStep(msg, protogo.StepType_SANDBOX_HANDLER_RECEIVE_TX_REQUEST, "")
	currentStatus = BeforeExecute

	defer func() {
		currentTxDuration.TotalDuration = time.Since(startTime).Nanoseconds()
		h.sandboxLogger.Debugf(currentTxDuration.ToString())
	}()

	args := msg.GetRequest().GetParameters()

	s := sdk.NewSDK(
		h.crossCtx,
		h.sendSyscallMsg,
		h.txId,
		h.originalTxId,
		h.chainId,
		h.contractName,
		h.contractAddr,
		h.contractLogger,
		h.sandboxLogger,
		args,
	)

	sdk.Instance = s
	sdk.Bulletproofs = sdk.NewBulletproofsInstance()
	sdk.Paillier = sdk.NewPaillierInstance()

	method := msg.Request.Method

	// compatible with version < 2.3.0 (method in parameters)
	if method == _invokeContract {
		if methodByte, ok := msg.Request.Parameters["method"]; ok {
			// found method, replace 'invoke_contract' method to real method
			method = string(methodByte)
		}
	}

	currentStatus = Executing
	var response protogo.Response
	switch method {
	case _initContract:
		response = h.contract.InitContract()
	case _upgradeContract:
		response = h.contract.UpgradeContract()
	default:
		response = h.contract.InvokeContract(method)
	}

	// construct complete message
	writeMap := s.GetWriteMap()
	readMap := s.GetReadMap()
	events := s.GetEvents()

	txResponse := &protogo.TxResponse{
		TxId:    h.txId,
		ChainId: h.chainId,
	}

	signal := &protogo.DockerVMMessage{
		ChainId:        h.chainId,
		TxId:           msg.TxId,
		CrossContext:   h.crossCtx,
		Type:           protogo.DockerVMType_COMPLETED,
		SysCallMessage: nil,
		Request:        nil,
		Response:       nil,
	}

	if response.Status == 0 {
		txResponse.Code = protogo.DockerVMCode_OK
		txResponse.Result = response.Payload
		txResponse.Message = "Success"
		txResponse.WriteMap = writeMap
		txResponse.ReadMap = readMap

		var responseEvents []*protogo.DockerContractEvent
		for _, event := range events {
			responseEvents = append(responseEvents, &protogo.DockerContractEvent{
				Topic:        event.Topic,
				ContractName: event.ContractName,
				Data:         event.Data,
			})
		}
		txResponse.Events = responseEvents

	} else {
		// If tx failed, we don't need to return wset and rset:
		// - wset: wset is always dropped for fail
		// - rset: rset can not be dropped for fail
		//  - from simContext: simContext already has such keys, value is not important
		//  - from wset: simContext don't have such keys, but it will not cause conflict for applying simContext,
		//               because even if other txs are written first, the read set here will eventually be dropped
		txResponse.Code = protogo.DockerVMCode_FAIL
		txResponse.Result = []byte(response.Message)
		txResponse.Message = "Fail"
		txResponse.WriteMap = nil
		txResponse.ReadMap = nil
		txResponse.Events = nil
		if method == _initContract || method == _upgradeContract {
			signal.Type = protogo.DockerVMType_ERROR
		}
	}

	respMsg := &protogo.DockerVMMessage{
		ChainId:        h.chainId,
		TxId:           h.txId,
		Type:           protogo.DockerVMType_TX_RESPONSE,
		Response:       txResponse,
		Request:        nil,
		SysCallMessage: nil,
	}

	currentStatus = AfterExecuted
	EnterNextStep(msg, protogo.StepType_SANDBOX_SEND_CHAIN_RESP, "")

	respMsg.StepDurations = msg.StepDurations
	h.sendResponse(respMsg)

	currentStatus = AfterSendResponse

	signal.StepDurations = msg.StepDurations
	// 发送finish信号
	h.txFinishMsgNotifyFunc(signal)

	currentStatus = Finished

	currentTxDuration.EndTime = time.Now().UnixNano()

	return nil
}

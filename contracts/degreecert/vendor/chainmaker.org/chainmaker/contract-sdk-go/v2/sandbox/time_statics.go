/*
Copyright (C) BABEC. All rights reserved.
Copyright (C) THL A29 Limited, a Tencent company. All rights reserved.

SPDX-License-Identifier: Apache-2.0
*/

package sandbox

import (
	"errors"
	"fmt"
	"strings"
	"time"

	"chainmaker.org/chainmaker/contract-sdk-go/v2/pb/protogo"
)

var (
	currentTxDuration = TxDuration{
		SysCallList: make([]*SysCallDuration, 0),
	}
	currentStatus = BeforeListening
)

type TxStatus string

const (
	BeforeListening        TxStatus = "before listening"
	BeforeReceive          TxStatus = "before receive"
	BeforeExecute          TxStatus = "before execute"
	Executing              TxStatus = "executing"
	WaitingSysCallResponse TxStatus = "waiting syscall response"
	AfterExecuted          TxStatus = "after executed"
	AfterSendResponse      TxStatus = "after send response"
	Finished               TxStatus = "finished"
)

type SysCallDuration struct {
	OpType        protogo.DockerVMType
	StartTime     int64
	TotalDuration int64
}

func (s *SysCallDuration) ToString() string {
	return fmt.Sprintf("%s start: %v, spend: %dμs; ",
		s.OpType.String(), time.Unix(s.StartTime/1e9, s.StartTime%1e9), s.TotalDuration/1000,
	)
}

type TxDuration struct {
	Tx                *protogo.DockerVMMessage
	StartTime         int64
	EndTime           int64
	TotalDuration     int64
	SysCallCnt        int32
	SysCallDuration   int64
	CrossCallCnt      int32
	CrossCallDuration int64
	SysCallList       []*SysCallDuration
}

func NewTxDuration(tx *protogo.DockerVMMessage, startTime int64) *TxDuration {
	return &TxDuration{
		Tx:        tx,
		StartTime: startTime,
	}
}

func (t *TxDuration) Reset(tx *protogo.DockerVMMessage, startTime int64) {
	t.Tx = tx
	t.StartTime = startTime
	t.EndTime = t.StartTime
	t.TotalDuration = 0
	t.SysCallCnt = 0
	t.SysCallDuration = 0
	t.CrossCallCnt = 0
	t.CrossCallDuration = 0
	t.SysCallList = t.SysCallList[:0]
}

func (t *TxDuration) ToString() string {
	return fmt.Sprintf("[%s] spend time: %dμs, syscall: %dμs(%d), cross contract: %dμs(%d)",
		t.Tx.TxId, t.TotalDuration/1000,
		t.SysCallDuration/1000, t.SysCallCnt,
		t.CrossCallDuration/1000, t.CrossCallCnt,
	)
}

func (t *TxDuration) PrintSysCallList() string {
	if len(t.SysCallList) == 0 {
		return "no syscalls"
	}
	var sb strings.Builder
	for _, sysCallTime := range t.SysCallList {
		sb.WriteString(sysCallTime.ToString())
	}
	return sb.String()
}

// StartSysCall start new sys call
func (t *TxDuration) StartSysCall(msg *protogo.DockerVMMessage) {
	currentStatus = WaitingSysCallResponse
	duration := &SysCallDuration{
		OpType:    msg.Type,
		StartTime: time.Now().UnixNano(),
	}
	t.SysCallList = append(t.SysCallList, duration)
}

// EndSysCall close new sys call
func (t *TxDuration) EndSysCall(msg *protogo.DockerVMMessage) error {
	currentStatus = Executing
	latestSysCall, err := t.GetLatestSysCall()
	if err != nil {
		return fmt.Errorf("failed to get latest sys call, %v", err)
	}
	latestSysCall.TotalDuration = time.Since(time.Unix(0, latestSysCall.StartTime)).Nanoseconds()
	t.addSysCallDuration(latestSysCall)
	return nil
}

// GetLatestSysCall returns latest sys call
func (t *TxDuration) GetLatestSysCall() (*SysCallDuration, error) {
	if len(t.SysCallList) == 0 {
		return nil, errors.New("sys call list length == 0")
	}
	return t.SysCallList[len(t.SysCallList)-1], nil
}

// addSysCallDuration add the count of system calls and the duration of system calls to the total record
func (t *TxDuration) addSysCallDuration(duration *SysCallDuration) {
	if duration == nil {
		return
	}
	switch duration.OpType {
	case protogo.DockerVMType_GET_BYTECODE_REQUEST:
	case protogo.DockerVMType_GET_STATE_REQUEST, protogo.DockerVMType_GET_BATCH_STATE_REQUEST,
		protogo.DockerVMType_CREATE_KV_ITERATOR_REQUEST, protogo.DockerVMType_CONSUME_KV_ITERATOR_REQUEST,
		protogo.DockerVMType_CREATE_KEY_HISTORY_ITER_REQUEST, protogo.DockerVMType_CONSUME_KEY_HISTORY_ITER_REQUEST,
		protogo.DockerVMType_GET_SENDER_ADDRESS_REQUEST:
		// record all syscalls except cross contract calls and txResponse
		t.SysCallCnt++
		t.SysCallDuration += duration.TotalDuration
	case protogo.DockerVMType_CALL_CONTRACT_REQUEST:
		// cross contract calls are recorded separately, which is different from syscall
		t.CrossCallCnt++
		t.CrossCallDuration += duration.TotalDuration
	default:
		return
	}
}

// EnterNextStep enter next duration tx step
func EnterNextStep(msg *protogo.DockerVMMessage, stepType protogo.StepType, log string) {
	if config.DisableSlowLog {
		return
	}
	if stepType != protogo.StepType_RUNTIME_PREPARE_TX_REQUEST {
		endTxStep(msg)
	}
	addTxStep(msg, stepType, log)
	if stepType == protogo.StepType_RUNTIME_HANDLE_TX_RESPONSE {
		endTxStep(msg)
	}
}

func addTxStep(msg *protogo.DockerVMMessage, stepType protogo.StepType, log string) {
	stepDur := &protogo.StepDuration{
		Type:      stepType,
		StartTime: time.Now().UnixNano(),
		Msg:       log,
	}
	msg.StepDurations = append(msg.StepDurations, stepDur)
}

func endTxStep(msg *protogo.DockerVMMessage) {
	if len(msg.StepDurations) == 0 {
		return
	}
	stepLen := len(msg.StepDurations)
	currStep := msg.StepDurations[stepLen-1]
	firstStep := msg.StepDurations[0]
	currStep.UntilDuration = time.Since(time.Unix(0, firstStep.StartTime)).Nanoseconds()
	currStep.StepDuration = time.Since(time.Unix(0, currStep.StartTime)).Nanoseconds()
}

// PrintTxSteps print all duration tx steps
func PrintTxSteps(msg *protogo.DockerVMMessage) string {
	var sb strings.Builder
	for _, step := range msg.StepDurations {
		sb.WriteString(fmt.Sprintf("<step: %q, start time: %v, step cost: %vms, until cost: %vms, msg: %s> ",
			step.Type, time.Unix(0, step.StartTime),
			time.Duration(step.StepDuration).Seconds()*1000,
			time.Duration(step.UntilDuration).Seconds()*1000,
			step.Msg))
	}
	return sb.String()
}

// PrintTxStepsWithTime print all duration tx steps with time limt
func PrintTxStepsWithTime(msg *protogo.DockerVMMessage, untilDuration time.Duration) (string, bool) {
	if len(msg.StepDurations) == 0 {
		return "", false
	}
	lastStep := msg.StepDurations[len(msg.StepDurations)-1]
	var sb strings.Builder
	if lastStep.UntilDuration > untilDuration.Nanoseconds() {
		sb.WriteString("slow tx overall: ")
		sb.WriteString(PrintTxSteps(msg))
		return sb.String(), true
	}
	for _, step := range msg.StepDurations {
		if step.StepDuration > time.Millisecond.Nanoseconds()*500 {
			sb.WriteString(fmt.Sprintf("slow tx at step %q, step cost: %vms: ",
				step.Type, time.Duration(step.StepDuration).Seconds()*1000))
			sb.WriteString(PrintTxSteps(msg))
			return sb.String(), true
		}
	}
	return "", false
}

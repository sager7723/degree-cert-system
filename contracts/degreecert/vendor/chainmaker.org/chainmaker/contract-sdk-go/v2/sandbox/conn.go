/*
Copyright (C) BABEC. All rights reserved.
Copyright (C) THL A29 Limited, a Tencent company. All rights reserved.

SPDX-License-Identifier: Apache-2.0
*/

package sandbox

import (
	"context"
	"fmt"
	"net"

	"chainmaker.org/chainmaker/contract-sdk-go/v2/pb/protogo"
	"google.golang.org/grpc"
)

const BufferSize = 1024 * 1024

func newUDSClient() (protogo.DockerVMRpc_DockerVMCommunicateClient, error) {

	dialOpts := []grpc.DialOption{
		grpc.WithInsecure(),
		grpc.WithContextDialer(func(ctx context.Context, sockAddress string) (net.Conn, error) {
			unixAddress, err := net.ResolveUnixAddr("unix", sockAddress)
			if err != nil {
				return nil, err
			}
			return net.DialUnix("unix", nil, unixAddress)
		}),
		grpc.FailOnNonTempDialError(true),
		grpc.WithDefaultCallOptions(
			grpc.MaxCallRecvMsgSize(config.ContractEngineClient.MaxRecvMsgSize),
			grpc.MaxCallSendMsgSize(config.ContractEngineClient.MaxSendMsgSize),
		),
		grpc.WithWriteBufferSize(BufferSize),
		grpc.WithReadBufferSize(BufferSize),
		grpc.WithInitialWindowSize(64 * 1024 * 1024),
		grpc.WithInitialConnWindowSize(64 * 1024 * 1024),
	}

	ctx, cancel := context.WithTimeout(context.Background(), dialTimeout)
	defer cancel()
	conn, err := grpc.DialContext(ctx, config.ContractEngineClient.EngineUDSSockPath, dialOpts...)
	if err != nil {
		return nil, err
	}
	return protogo.NewDockerVMRpcClient(conn).DockerVMCommunicate(context.Background())
}

// newRuntimeConn create rpc connection
func newRuntimeConn() (protogo.DockerVMRpc_DockerVMCommunicateClient, error) {

	var conn *grpc.ClientConn
	var err error

	dialOpts := []grpc.DialOption{
		grpc.WithInsecure(),
		grpc.WithDefaultCallOptions(
			grpc.MaxCallRecvMsgSize(config.RuntimeClient.MaxRecvMsgSize),
			grpc.MaxCallSendMsgSize(config.RuntimeClient.MaxSendMsgSize),
		),
		grpc.WithWriteBufferSize(BufferSize),
		grpc.WithReadBufferSize(BufferSize),
		grpc.WithInitialWindowSize(64 * 1024 * 1024),
		grpc.WithInitialConnWindowSize(64 * 1024 * 1024),
	}

	if config.RuntimeClient.RuntimeRPCProtocolType == TCP {
		url := fmt.Sprintf("%s:%s", config.RuntimeClient.RuntimeHost, config.RuntimeClient.RuntimePort)
		conn, err = grpc.Dial(url, dialOpts...)
		if err != nil {
			return nil, err
		}

	} else {
		dialOpts = append(
			dialOpts,
			grpc.WithContextDialer(
				func(ctx context.Context, sock string) (net.Conn, error) {
					unixAddress, _ := net.ResolveUnixAddr("unix", sock)
					conn, err := net.DialUnix("unix", nil, unixAddress)
					return conn, err
				},
			),
		)

		conn, err = grpc.DialContext(context.Background(), config.RuntimeClient.RuntimeUDSSockPath, dialOpts...)
		if err != nil {
			return nil, err
		}
	}

	return protogo.NewDockerVMRpcClient(conn).DockerVMCommunicate(context.Background())
}

// GetClientStream get rpc stream
func GetClientStream(conn *grpc.ClientConn) (protogo.DockerVMRpc_DockerVMCommunicateClient, error) {
	return protogo.NewDockerVMRpcClient(conn).DockerVMCommunicate(context.Background())
}

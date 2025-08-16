// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract ApiCallsRegistry {
    event ApiCallProved(string indexed callId, string requestHash, string responseHash, address indexed emitter);

    function emitApiCallProved(string calldata callId, string calldata requestHash, string calldata responseHash) external {
        emit ApiCallProved(callId, requestHash, responseHash, msg.sender);
    }
}


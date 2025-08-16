import { newMockEvent } from "matchstick-as"
import { ethereum, Address } from "@graphprotocol/graph-ts"
import { ApiCallProved } from "../generated/ApiCallsRegistry/ApiCallsRegistry"

export function createApiCallProvedEvent(
  callId: string,
  requestHash: string,
  responseHash: string,
  emitter: Address
): ApiCallProved {
  let apiCallProvedEvent = changetype<ApiCallProved>(newMockEvent())

  apiCallProvedEvent.parameters = new Array()

  apiCallProvedEvent.parameters.push(
    new ethereum.EventParam("callId", ethereum.Value.fromString(callId))
  )
  apiCallProvedEvent.parameters.push(
    new ethereum.EventParam(
      "requestHash",
      ethereum.Value.fromString(requestHash)
    )
  )
  apiCallProvedEvent.parameters.push(
    new ethereum.EventParam(
      "responseHash",
      ethereum.Value.fromString(responseHash)
    )
  )
  apiCallProvedEvent.parameters.push(
    new ethereum.EventParam("emitter", ethereum.Value.fromAddress(emitter))
  )

  return apiCallProvedEvent
}

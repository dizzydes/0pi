import { ApiCallProved } from "../generated/ApiCallsRegistry/ApiCallsRegistry"
import { ApiCall } from "../generated/schema"

export function handleApiCallProved(event: ApiCallProved): void {
  const id = event.transaction.hash.toHex() + ":" + event.logIndex.toString()
  let entity = new ApiCall(id)
  entity.callId = event.params.callId
  entity.requestHash = event.params.requestHash
  entity.responseHash = event.params.responseHash
  entity.emitter = event.params.emitter
  entity.txHash = event.transaction.hash
  entity.blockNumber = event.block.number
  entity.timestamp = event.block.timestamp
  entity.save()
}


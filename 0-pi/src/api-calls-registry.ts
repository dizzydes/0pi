import { ApiCallProved as ApiCallProvedEvent } from "../generated/ApiCallsRegistry/ApiCallsRegistry"
import { ApiCallProved } from "../generated/schema"

export function handleApiCallProved(event: ApiCallProvedEvent): void {
  let entity = new ApiCallProved(
    event.transaction.hash.concatI32(event.logIndex.toI32())
  )
  entity.callId = event.params.callId
  entity.requestHash = event.params.requestHash
  entity.responseHash = event.params.responseHash
  entity.emitter = event.params.emitter

  entity.blockNumber = event.block.number
  entity.blockTimestamp = event.block.timestamp
  entity.transactionHash = event.transaction.hash

  entity.save()
}

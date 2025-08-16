import {
  assert,
  describe,
  test,
  clearStore,
  beforeAll,
  afterAll
} from "matchstick-as/assembly/index"
import { Address } from "@graphprotocol/graph-ts"
import { ApiCallProved } from "../generated/schema"
import { ApiCallProved as ApiCallProvedEvent } from "../generated/ApiCallsRegistry/ApiCallsRegistry"
import { handleApiCallProved } from "../src/api-calls-registry"
import { createApiCallProvedEvent } from "./api-calls-registry-utils"

// Tests structure (matchstick-as >=0.5.0)
// https://thegraph.com/docs/en/subgraphs/developing/creating/unit-testing-framework/#tests-structure

describe("Describe entity assertions", () => {
  beforeAll(() => {
    let callId = "Example string value"
    let requestHash = "Example string value"
    let responseHash = "Example string value"
    let emitter = Address.fromString(
      "0x0000000000000000000000000000000000000001"
    )
    let newApiCallProvedEvent = createApiCallProvedEvent(
      callId,
      requestHash,
      responseHash,
      emitter
    )
    handleApiCallProved(newApiCallProvedEvent)
  })

  afterAll(() => {
    clearStore()
  })

  // For more test scenarios, see:
  // https://thegraph.com/docs/en/subgraphs/developing/creating/unit-testing-framework/#write-a-unit-test

  test("ApiCallProved created and stored", () => {
    assert.entityCount("ApiCallProved", 1)

    // 0xa16081f360e3847006db660bae1c6d1b2e17ec2a is the default address used in newMockEvent() function
    assert.fieldEquals(
      "ApiCallProved",
      "0xa16081f360e3847006db660bae1c6d1b2e17ec2a-1",
      "callId",
      "Example string value"
    )
    assert.fieldEquals(
      "ApiCallProved",
      "0xa16081f360e3847006db660bae1c6d1b2e17ec2a-1",
      "requestHash",
      "Example string value"
    )
    assert.fieldEquals(
      "ApiCallProved",
      "0xa16081f360e3847006db660bae1c6d1b2e17ec2a-1",
      "responseHash",
      "Example string value"
    )
    assert.fieldEquals(
      "ApiCallProved",
      "0xa16081f360e3847006db660bae1c6d1b2e17ec2a-1",
      "emitter",
      "0x0000000000000000000000000000000000000001"
    )

    // More assert options:
    // https://thegraph.com/docs/en/subgraphs/developing/creating/unit-testing-framework/#asserts
  })
})

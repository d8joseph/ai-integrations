import type { EngineInterface, Register, ToolCallResult, TurnStepChunk, TurnStepResult } from 'claude-code'

/*
  Every model call and tool call is checkpointed in the Temporal Workflow, and replayed from it when
  the activity retries. If the mod can't reach the Workflow, it stops the turn. The activity sees
  the failed call, fails the attempt, and Temporal retries it, so the mod does not retry itself.

  The activity starts Claude Code as a subprocess, and this mod runs in that subprocess, apart
  from the Python process that holds the activity's Temporal client. The `workflow_handle` MCP server
  exists to give this mod that client. It runs in the Python process, and each tool calls the
  Workflow through the client.

  checkpoint: saves a result with the Workflow's `checkpoint` signal.
  replay: reads a checkpointed result with the Workflow's `replay` query, or null if none.
  The signal returns once Temporal has recorded it, without waiting for the Workflow to run.
*/

// turnId is left out: it is minted randomly on each run, so a replay uses the current one.
type ModelCall = { chunks: TurnStepChunk[]; result: Omit<TurnStepResult, 'turnId'> }

type CheckpointedCall = ToolCallResult | ModelCall

function isDataChunk(chunk: TurnStepChunk) {
  return chunk.kind !== 'engine'
}

let runningTurnId = ''

async function callWorkflow($: EngineInterface, tool: 'checkpoint' | 'replay', args: Record<string, unknown>) {
  try {
    const r = await $.mcp.call('workflow_handle', tool, args)
    if (r.isError) throw new Error(`workflow_handle ${tool} failed`)
    return r
  } catch (err) {
    await $.turn.abort({ turnId: runningTurnId })
    throw err
  }
}

async function checkpoint($: EngineInterface, id: string, value: CheckpointedCall) {
  await callWorkflow($, 'checkpoint', { id, value })
}

async function replay<T extends CheckpointedCall>($: EngineInterface, id: string): Promise<T | null> {
  const r = await callWorkflow($, 'replay', { id })
  const { text } = r.content[0] as { text: string }
  return JSON.parse(text)
}


// A subagent gets a new agentId on every run, so its model calls are keyed by the Agent tool call
// that spawned it instead: that tool_use_id comes from a model response, which a retry replays.
const subagentToolUseIds = new Map<string, string>([["", "main"]])

export const register: Register = (on) => {
  on('turn.start', ($, e, next) => {
    runningTurnId = e.turnId
    return next(e)
  })

  on('agent.spawn', async ($, e, next) => {
    const r = await next(e)
    if (r.agentId !== undefined) {
      subagentToolUseIds.set(r.agentId, e.tool_use_id)
    }
    return r
  })

  on('tool.call', async ($, e, next) => {
    if (e.tool.startsWith('mcp__workflow_handle__')) { // Only this mod is allowed to call the workflow_handle MCP server.
      return next.origin.plugin === 'engine' ? { deny: `${e.tool} is internal` } : next(e)
    }

    const deterministicId = `tool-${e.tool_use_id}` // tool_use_id comes from the model's response, which a retry replays from the Workflow.

    const saved = await replay<ToolCallResult>($, deterministicId)
    if (saved !== null) {
      return saved
    }

    const r = await next(e)

    // A hook can't mark a result as an error, only answer `{ result }` or `{ deny }`. So an errored
    // call replays as a deny, which the model reads as the same error.
    const errorText = r.deny ?? (r.isError ? r.text ?? '' : undefined)
    const replayable: ToolCallResult = errorText !== undefined ? { deny: errorText } : { result: r.result }

    await checkpoint($, deterministicId, replayable)
    return r
  }).catch(($, _e, next) => ({ deny: `${$.plugin.name}: ${next.error.message}` }))

  on('turn.step', async function* ($, e, next) {
    const agentId = subagentToolUseIds.get(e.agentId || '')
    const turns = await $.session.turns()
    const deterministicId = `model-${agentId}-${turns}-${e.index}`

    const saved = await replay<ModelCall>($, deterministicId)
    if (saved !== null) {
      yield* saved.chunks
      return { ...saved.result, turnId: e.turnId }
    }

    const chunks: TurnStepChunk[] = []
    const stream = next(e)
    for await (const chunk of stream) {
      if (isDataChunk(chunk)) {
        const { ref, ...data } = chunk
        chunks.push(data)
      }
      yield chunk
    }
    const result = await stream.result
    const { turnId, ...rest } = result
    await checkpoint($, deterministicId, { chunks, result: rest })
    return result
  }).catch(async function* ($, e) {
    return { turnId: e.turnId, index: e.index, answer: '', toolUses: [], stopReason: null, usage: null }
  })
}

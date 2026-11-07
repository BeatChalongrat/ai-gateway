package service

// FIX105 — two refusals that ask for different things.
//
// "This conversation is too long" tells the person to shorten the
// conversation or start a new one. Since FIX105 no conversation can reach a
// state where trimming cannot produce a request that fits — except one: the
// newest message alone is larger than the whole messages budget. No trim
// removes the message being sent, so the provider refuses it however short
// the rest of the conversation is, and the only useful advice is about THAT
// message.
//
// Nothing is refused here on an estimate. The estimator over-counts Thai
// about three times on some tokenizers, so refusing up front would turn away
// messages that fit. The request is sent; only if the provider refuses it
// with context_length_exceeded, and the compiler had already seen that the
// newest message alone did not fit, is the refusal renamed.

// CodeMessageTooLong is the error code the client localises.
const CodeMessageTooLong = "message_too_long"

const messageTooLongText = "This message alone is too long for this model. Shorten it or split it into parts; the rest of the conversation is unaffected."

// messageTooLongOr returns pe unchanged unless it is a context-length
// refusal of a request whose newest message alone exceeded the budget.
func messageTooLongOr(pe *StreamProviderError, newestOverBudget bool) *StreamProviderError {
	if pe == nil || !newestOverBudget || pe.Code != "context_length_exceeded" {
		return pe
	}
	out := *pe
	out.Code = CodeMessageTooLong
	out.Message = messageTooLongText
	return &out
}

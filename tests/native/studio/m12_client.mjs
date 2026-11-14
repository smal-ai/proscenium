// Match the accepted product command identity precedence, not UI context precedence.
export function commandIdentity(body) {
  return body.operation_id ?? body.request_id ?? body.publication_id ?? body.review_id ?? body.context_id;
}

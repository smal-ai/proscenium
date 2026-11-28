import type { ReviewTicket } from "../../api";

export function reviewDisplayName(ticket: ReviewTicket): string {
  return ticket.title?.trim() || ticket.review_id;
}

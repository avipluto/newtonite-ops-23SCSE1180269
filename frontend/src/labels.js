// Human-readable names for the values the API uses.

export const STATUS = {
  open: "Open",
  in_progress: "In progress",
  awaiting_approval: "Awaiting approval",
  resolved: "Resolved",
  closed: "Closed",
};

export const PRIORITY = { low: "Low", medium: "Medium", high: "High", urgent: "Urgent" };

export const TYPE = {
  incident: "Incident",
  customer_issue: "Customer issue",
  payment: "Payment",
  compliance: "Compliance",
  task: "Task",
};

export const formatDate = (iso) => (iso ? new Date(iso).toLocaleString() : "");

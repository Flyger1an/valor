console.log(
  JSON.stringify({
    service: "valor-worker",
    status: "ready",
    note: "Alert delivery and ingestion workers are wired as safe VM service placeholders. Real queues should consume Redis/BullMQ jobs before sending external alerts.",
    timestamp: new Date().toISOString(),
  }),
);

setInterval(() => {
  console.log(
    JSON.stringify({
      service: "valor-worker",
      status: "heartbeat",
      timestamp: new Date().toISOString(),
    }),
  );
}, 60_000);

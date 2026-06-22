console.log(
  JSON.stringify({
    service: "valor-scheduler",
    status: "ready",
    note: "Scheduler placeholder is safe-by-default. Add ingestion, alert enqueue, and digest jobs here.",
    timestamp: new Date().toISOString(),
  }),
);

setInterval(() => {
  console.log(
    JSON.stringify({
      service: "valor-scheduler",
      status: "tick",
      timestamp: new Date().toISOString(),
    }),
  );
}, 60_000);

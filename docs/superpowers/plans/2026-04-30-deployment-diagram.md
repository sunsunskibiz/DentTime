# Deployment Diagram Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `c4/denttime_c4_deployment.puml` — a C4 Deployment Diagram showing the future GKE Autopilot production target, covering both the ML training pipeline (Airflow, MLflow, GCS artifact publish) and the model serving infrastructure (Istio, KServe, Triton, monitoring).

**Architecture:** Environment-first layout with top-level `Deployment_Node`s mirroring real infrastructure zones (GKE cluster, GCS, CMS). The GKE cluster is split into two Kubernetes namespaces: `denttime-training` and `denttime-serving`. GCS sits as the artifact bridge between the two namespaces.

**Tech Stack:** PlantUML, C4-PlantUML stdlib (`C4_Deployment.puml`), PlantUML online renderer or local PlantUML JAR for validation.

---

## Files

- **Create:** `c4/denttime_c4_deployment.puml`

---

### Task 1: Write the deployment diagram skeleton and validate it renders

**Files:**
- Create: `c4/denttime_c4_deployment.puml`

- [ ] **Step 1: Create the file with the C4 Deployment header and verify it renders**

Create `c4/denttime_c4_deployment.puml` with:

```plantuml
@startuml denttime_c4_deployment
!include https://raw.githubusercontent.com/plantuml-stdlib/C4-PlantUML/master/C4_Deployment.puml

LAYOUT_WITH_LEGEND()

title [Deployment Diagram] DentTime — Future Production Target (GKE Autopilot)

@enduml
```

Validate by pasting into https://www.plantuml.com/plantuml/uml/ — it should render an empty diagram with a legend and title.

- [ ] **Step 2: Add external nodes — CMS and GCS**

Replace the `title` line onwards with:

```plantuml
title [Deployment Diagram] DentTime — Future Production Target (GKE Autopilot)

' ===== External: Clinic Management System =====
Deployment_Node(nodeCms, "Clinic Management System", "MySQL / PostgreSQL") {
    Container_Instance(cmsDb, "Appointment DB", "MySQL / PostgreSQL", "Source of historical appointment\nand treatment records (~1M rows, 2025).")
}

' ===== Google Cloud Storage =====
Deployment_Node(nodeGcs, "Google Cloud Storage", "GCS Bucket: denttime-models") {
    InfrastructureNode(gcsArtifacts, "Model Artifacts Store", "ONNX format", "Versioned ONNX model files.\nNaming: denttime_xgb_v{M}.{m}_{YYYYMM}.onnx\nRetains last 2 versions as fallback.")
}

@enduml
```

Validate: two nodes appear in the rendered diagram.

- [ ] **Step 3: Add GKE cluster with denttime-training namespace**

Insert before `@enduml`:

```plantuml
' ===== GKE Autopilot Cluster =====
Deployment_Node(nodeGke, "GKE Autopilot Cluster", "Google Kubernetes Engine Autopilot") {

    ' ── Training Namespace ──────────────────────────────
    Deployment_Node(nsTraining, "Namespace: denttime-training", "Kubernetes Namespace") {

        Deployment_Node(podPostgres, "Pod: postgres", "Kubernetes Pod") {
            Container_Instance(postgres, "PostgreSQL 15", "PostgreSQL 15", "Airflow metadata store\n+ MLflow backend store.")
        }

        Deployment_Node(podMlflow, "Pod: mlflow", "Kubernetes Pod") {
            Container_Instance(mlflow, "MLflow Tracking Server", "MLflow 2.x", "Tracks experiments, metrics,\nand model versions.\nServes artifact store API.")
        }

        Deployment_Node(podAirflowWeb, "Pod: airflow-webserver", "Kubernetes Pod") {
            Container_Instance(airflowWeb, "Airflow Webserver", "Apache Airflow 2.x", "DAG management UI on :8080.\nTriggers feature engineering\nand retrain DAGs.")
        }

        Deployment_Node(podAirflowSched, "Pod: airflow-scheduler", "Kubernetes Pod") {
            Container_Instance(airflowSched, "Airflow Scheduler", "Apache Airflow 2.x", "Executes DAGs:\nfeature_engineering_dag,\ndenttime_retrain_dag.\nPublishes approved ONNX model\nto GCS via MLflow API.")
        }
    }

}
```

Validate: GKE cluster node appears with the four training pods nested inside.

- [ ] **Step 4: Add denttime-serving namespace inside the GKE cluster**

Inside `Deployment_Node(nodeGke, ...)` block, after the training namespace closing brace, insert:

```plantuml
    ' ── Serving Namespace ───────────────────────────────
    Deployment_Node(nsServing, "Namespace: denttime-serving", "Kubernetes Namespace") {

        InfrastructureNode(istio, "Istio Ingress Gateway", "Istio / Envoy", "Entry point for all inference traffic.\nTLS termination + load balancing.\nMirrors requests to shadow server.")

        Deployment_Node(podFrontend, "Pod: frontend", "Kubernetes Pod") {
            Container_Instance(frontend, "DentTime Frontend", "React 19 + Vite / Nginx", "Serves the clinic staff booking UI.\nCalls POST /predict via Istio.")
        }

        Deployment_Node(podRouter, "Pod: kserve-traffic-router", "KServe InferenceService") {
            Container_Instance(router, "Traffic Router", "KServe Traffic Splitting", "Controls canary rollout:\nShadow phase → 10% canary → 100% prod.\nRollback if F1 drops > 5% within 24h.")
        }

        Deployment_Node(podTritonProd, "Pod: triton-prod", "Kubernetes Pod (replicated)") {
            Container_Instance(tritonProd, "Triton Inference Server (Prod)", "NVIDIA Triton + ONNX Runtime", "Serves production ONNX model.\np99 latency target: < 1s.\nHot-reload for zero-downtime model swap.")
        }

        Deployment_Node(podTritonShadow, "Pod: triton-shadow", "Kubernetes Pod") {
            Container_Instance(tritonShadow, "Triton Inference Server (Shadow)", "NVIDIA Triton + ONNX Runtime", "Mirrors production requests silently.\nUsed for model candidate evaluation.\nDoes NOT return results to user.")
        }

        Deployment_Node(podEvalLogger, "Pod: eval-logger", "Kubernetes Pod") {
            Container_Instance(evalLogger, "Evaluation Logger", "Prometheus", "Compares prod vs shadow predictions.\nTracks: Macro F1, under-estimation rate, MAE.\nDecides promote / rollback signal.")
        }

        Deployment_Node(podGrafana, "Pod: grafana", "Kubernetes Pod") {
            Container_Instance(grafana, "Grafana", "Grafana 11.x", "Monitoring dashboards.\nDisplays: drift alerts, latency,\nF1 trend, PSI per feature.")
        }

        InfrastructureNode(hpa, "KPA / HPA Auto-Scaler", "KServe KPA + K8s HPA", "Monitors RPS and CPU on triton-prod.\nAuto-scales replicas to maintain\np99 latency < 1s.")
    }
```

Validate: both namespaces visible inside GKE cluster node.

- [ ] **Step 5: Add all relationships**

Insert before `@enduml`:

```plantuml
' ===== Training Pipeline (offline / batch) =====
Rel(cmsDb, airflowSched, "Raw appointment records", "SQL / CSV Export")
Rel(airflowSched, postgres, "Airflow metadata", "SQL")
Rel(airflowSched, mlflow, "Log experiments & metrics", "MLflow API (HTTP)")
Rel(mlflow, postgres, "MLflow backend store", "SQL")
Rel(mlflow, gcsArtifacts, "Publish approved ONNX model", "GCS API")

' ===== Serving Pipeline (online / on-demand) =====
Rel(frontend, istio, "POST /predict\n(treatment text, tooth_no,\ndoctor_id, time context)", "HTTPS / JSON")
Rel(istio, router, "Forward inference request", "gRPC")
Rel(istio, tritonShadow, "Mirror request (silent copy)", "gRPC")
Rel(router, tritonProd, "Route production traffic", "gRPC")
Rel(tritonProd, frontend, "Duration slot response\n(15/30/45/60/90/105 min)", "gRPC / JSON")
Rel(gcsArtifacts, tritonProd, "Hot-reload ONNX model\n(zero-downtime update)", "GCS API")
Rel(gcsArtifacts, tritonShadow, "Hot-reload ONNX model", "GCS API")

' ===== Monitoring & Feedback Loop =====
Rel(tritonProd, evalLogger, "Production predictions & metrics", "Prometheus scrape")
Rel(tritonShadow, evalLogger, "Shadow predictions & metrics", "Prometheus scrape")
Rel(evalLogger, router, "Promote / rollback signal", "Internal API")
Rel(tritonProd, hpa, "Resource utilization metrics", "K8s Metrics API")
Rel(hpa, nodeGke, "Request additional compute nodes", "GKE Autopilot API")
Rel(grafana, evalLogger, "Query metrics", "Prometheus HTTP")
```

Validate: all arrows appear connecting training pipeline, serving pipeline, and monitoring loop.

- [ ] **Step 6: Do a final visual check**

Open the rendered diagram and verify:
- Training namespace contains: `postgres`, `mlflow`, `airflow-webserver`, `airflow-scheduler`
- Serving namespace contains: `istio`, `frontend`, `kserve-traffic-router`, `triton-prod`, `triton-shadow`, `eval-logger`, `grafana`, `hpa`
- GCS sits as artifact bridge: MLflow → GCS → Triton (prod + shadow)
- Monitoring loop visible: triton-prod/shadow → eval-logger → router
- Auto-scaler arrow from triton-prod → hpa → GKE cluster

- [ ] **Step 7: Commit**

```bash
git add "c4/denttime_c4_deployment.puml"
git commit -m "feat(c4): add deployment diagram for future GKE Autopilot target"
```

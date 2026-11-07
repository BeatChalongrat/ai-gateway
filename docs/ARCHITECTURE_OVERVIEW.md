# Architecture Overview

## Product Positioning
Secure AI Gateway for organizations that need:
- one endpoint for many LLM providers
- strict data protection before external AI calls
- governance by tenant / project / department / region
- centralized usage, cost, and audit visibility

## Main planes
1. Control plane
   - tenant/project/provider/model/policy management
2. Data plane
   - live inference requests and responses
3. Audit plane
   - immutable or durable audit events and policy decisions

## Main runtime flow
1. authenticate request
2. resolve tenant/project
3. validate payload
4. classify input
5. run DLP / PII scan
6. apply policy profile
7. select provider/model
8. dispatch to adapter
9. scan response
10. store usage + audit events
11. return normalized response

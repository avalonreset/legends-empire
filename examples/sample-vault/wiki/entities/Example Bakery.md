---
type: entity
title: Example Bakery
status: evergreen
created: 2026-07-11
updated: 2026-07-11
tags:
  - entity
  - sample
entity_id: example-bakery
aliases:
  - Example Bakery
  - The Bakery
  - Example Bakehouse
fact.domain: example.com
fact.phone: 555-0100
fact.hours: 7am-6pm
fact.wholesale_discount: 20%
fact.annual_flour_usage: 12000kg
evidence.annual_flour_usage: raw:sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
fact.payment_gateway: secret-ref:bakery.env#STRIPE_GATEWAY_KEY
certified_owner:
  - fact.domain
  - fact.phone
certified_evidence:
  - fact.annual_flour_usage
certified_on: 2026-11-14
---

# Example Bakery

A fictional artisan bakery demonstrating the Legends Empire Ontology Engine.

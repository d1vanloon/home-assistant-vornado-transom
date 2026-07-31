"""Alexa Nexus GraphQL queries and mutations."""

QUERY_SMART_HOME_ENDPOINTS = """
query Endpoints {
  endpoints {
    items {
      endpointId
      id
      friendlyName
      displayCategories {
        primary {
          value
        }
      }
      legacyIdentifiers {
          dmsIdentifier {
              deviceType {
                  type
                  value {
                      text
                  }
              }
              deviceSerialNumber {
                  type
                  value {
                      text
                  }
              }
          }
      }
      legacyAppliance {
        applianceId
        applianceTypes
        endpointTypeId
        friendlyName
        friendlyDescription
        manufacturerName
        connectedVia
        modelName
        entityId
        actions
        mergedApplianceIds
        capabilities
        applianceNetworkState
        version
        isEnabled
        customerDefinedDeviceType
        customerPreference
        alexaDeviceIdentifierList
        aliases
        driverIdentity
        additionalApplianceDetails
        isConsentRequired
        applianceKey
        appliancePairs
        deduplicatedPairs
        entityPairs
        deduplicatedAliasesByEntityId
        relations
      }
      serialNumber {
        value {
          text
        }
      }
      enablement
      model {
        value {
          text
        }
      }
      manufacturer {
        value {
          text
        }
      }
      features {
        name
        operations {
          name
        }
      }
    }
  }
}
"""

QUERY_FAN_STATE = """
query getFanStates(
  $endpointId: String!
) {
  endpoint(id: $endpointId) {
    features {
      name
      instance
      properties {
        name
        ... on Power {
          powerStateValue
        }
        ... on Mode {
          modeValue {
            value
          }
        }
        ... on RangeValue {
          rangeValue {
            value
          }
        }
      }
      configuration {
        ... on RangeConfiguration {
          friendlyName {
            value {
              text
            }
          }
        }
      }
    }
  }
}
"""

MUTATION_SET_ENDPOINT_FEATURES = """
mutation updatePowerFeatureForEndpoints($featureControlRequests: [FeatureControlRequest!]!) {
  setEndpointFeatures(
    setEndpointFeaturesInput: {
      featureControlRequests: $featureControlRequests
    }
  ) {
    featureControlResponses {
      endpointId
      featureOperationName
      __typename
    }
    errors {
      endpointId
      code
      __typename
    }
    __typename
  }
}
"""

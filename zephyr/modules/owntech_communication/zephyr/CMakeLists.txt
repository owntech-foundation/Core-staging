if(CONFIG_OWNTECH_COMMUNICATION)
  # Select directory to add to the include path
  zephyr_include_directories(./public_api)

  # Define the current folder as a Zephyr library
  zephyr_library()

  zephyr_library_sources(
    public_api/CommunicationAPI.cpp
  )

  if (CONFIG_OWNTECH_COMMUNICATION_ENABLE_ANALOG)
    zephyr_library_sources(
      src/AnalogCommunication.cpp
    )
  endif()

  # ThingSet over CAN uses the controller of the `thingset,can` chosen node: stop here with a readable message rather
  # than let the ThingSet SDK fail on an undeclared __device_dts_ord_N when that node is missing or disabled.
  if (CONFIG_OWNTECH_COMMUNICATION_ENABLE_CAN AND NOT CONFIG_OWNTECH_DT_THINGSET_CAN_ENABLED)
    message(FATAL_ERROR
      "CONFIG_OWNTECH_COMMUNICATION_ENABLE_CAN=y, but the device tree has no enabled `thingset,can` chosen node "
      "(shield '${SHIELD}'). ThingSet over CAN needs it: Twist 1.4.x and Ownverter have it, Twist 1.3.0 does not. "
      "Remove CONFIG_OWNTECH_COMMUNICATION_ENABLE_CAN from app.conf, or choose and enable a CAN controller as "
      "`thingset,can` in an app.overlay.")
  endif()
  if (CONFIG_OWNTECH_COMMUNICATION_ENABLE_CAN)
    zephyr_library_sources(
      src/CanCommunication.cpp
      src/thingset_can.c
    )
  endif()

  if (CONFIG_OWNTECH_COMMUNICATION_ENABLE_RS485)
    zephyr_library_sources(
      src/Rs485Communication.cpp
      src/Rs485.cpp
    )
  endif()

  if (CONFIG_OWNTECH_COMMUNICATION_ENABLE_SYNC)
    zephyr_library_sources(
      src/SyncCommunication.cpp
    )
  endif()

endif()
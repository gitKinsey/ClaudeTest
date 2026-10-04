#pragma once
#include "Arduino.h"
typedef struct { char version[32]; char project_name[32]; char time[16]; char date[16]; } esp_app_desc_t;
typedef struct { int type; int subtype; uint32_t address; uint32_t size; char label[17]; } esp_partition_t;
#define ESP_OK 0
const esp_partition_t* esp_ota_get_running_partition();
const esp_partition_t* esp_ota_get_next_update_partition(const esp_partition_t* from);
int esp_ota_get_partition_description(const esp_partition_t* p, esp_app_desc_t* d);
int esp_ota_set_boot_partition(const esp_partition_t* p);

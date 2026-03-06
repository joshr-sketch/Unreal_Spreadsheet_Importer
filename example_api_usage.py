import unreal


def cast(object_to_cast, object_class):
	try:
		return object_class.cast(object_to_cast)
	except:
		return None

ides_subsystem = unreal.get_editor_subsystem(unreal.ItemDefinitionEditorSubsystem)
if ides_subsystem is None:
    raise RuntimeError("MyModTest: ItemDefinitionEditorSubsystem not found.")




asset_path = "/HuskyGameplay/ItemDefinitions/Resources/WID_Husky_CopperOre.WID_Husky_CopperOre"
asset = unreal.load_asset(asset_path)

item_definition = cast(asset, unreal.ItemDefinitionBase)
if item_definition is None:
    raise RuntimeError("MyModTest: The asset path do not point on a item definition asset.")

ides_subsystem = unreal.get_editor_subsystem(unreal.ItemDefinitionEditorSubsystem)
if ides_subsystem is None:
    raise RuntimeError("MyModTest: ItemDefinitionEditorSubsystem not found.")

print("Start test")

component_types = ides_subsystem.get_all_component_data_type(item_definition)
print(f"WID_Husky_CopperOre item component data list: {component_types}")

#MaxStackSize Component
max_stack_size = ides_subsystem.get_property_value_scalable_float(item_definition, "/Script/ItemizationCoreRuntime.ItemComponentData_MaxStackSize", "MaxStackSize")
print(f"ItemComponentData_MaxStackSize max_stack_size(FScalableFloat) is: {max_stack_size}")
max_stack_size.value = 23.32
ides_subsystem.set_property_value_scalable_float(item_definition, "/Script/ItemizationCoreRuntime.ItemComponentData_MaxStackSize", "MaxStackSize", max_stack_size)
max_stack_size_2 = ides_subsystem.get_property_value_scalable_float(item_definition, "/Script/ItemizationCoreRuntime.ItemComponentData_MaxStackSize", "MaxStackSize")
print(f"ItemComponentData_MaxStackSize max_stack_size(FScalableFloat) set to: {max_stack_size_2}")

#Pickup Component
pickup_component_type = "/Script/FortniteGame.FortItemComponentData_Pickup"
can_be_drop_from_inventory = ides_subsystem.get_property_value_bool(item_definition, pickup_component_type, "bCanBeDroppedFromInventory")
print(f"FortItemComponentData_Pickup can_be_drop_from_inventory(bool) is: {can_be_drop_from_inventory}")
can_be_drop_from_inventory = not can_be_drop_from_inventory
ides_subsystem.set_property_value_bool(item_definition, pickup_component_type, "bCanBeDroppedFromInventory", can_be_drop_from_inventory)
can_be_drop_from_inventory = ides_subsystem.get_property_value_bool(item_definition, pickup_component_type, "bCanBeDroppedFromInventory")
print(f"FortItemComponentData_Pickup can_be_drop_from_inventory(bool) set to: {can_be_drop_from_inventory}")

mini_map_viewableDistance = ides_subsystem.get_property_value_float(item_definition, pickup_component_type, "MiniMapViewableDistance")
print(f"FortItemComponentData_Pickup mini_map_viewableDistance(float) is: {mini_map_viewableDistance}")
mini_map_viewableDistance = 12.7
ides_subsystem.set_property_value_float(item_definition, pickup_component_type, "MiniMapViewableDistance", mini_map_viewableDistance)
mini_map_viewableDistance = ides_subsystem.get_property_value_float(item_definition, pickup_component_type, "MiniMapViewableDistance")
print(f"FortItemComponentData_Pickup mini_map_viewableDistance(float) set to: {mini_map_viewableDistance}")

tint_r_float = ides_subsystem.get_property_value_float(item_definition, pickup_component_type, "MiniMapIconBrush.Tint.SpecifiedColor.R")
print(f"FortItemComponentData_Pickup MiniMapIconBrush.Tint.SpecifiedColor.R(float) is: {tint_r_float}")
tint_r_float = 0.01
ides_subsystem.set_property_value_float(item_definition, pickup_component_type, "MiniMapIconBrush.Tint.SpecifiedColor.R", tint_r_float)
tint_r_float = ides_subsystem.get_property_value_float(item_definition, pickup_component_type, "MiniMapIconBrush.Tint.SpecifiedColor.R")
print(f"FortItemComponentData_Pickup MiniMapIconBrush.Tint.SpecifiedColor.R(float) set to: {tint_r_float}")

ftext_format = ides_subsystem.get_property_value_generic_text(item_definition, pickup_component_type, "OwnerPickupText")
print(f"FText format: {ftext_format}")
tsoft_object_ptr_format = ides_subsystem.get_property_value_generic_text(item_definition, pickup_component_type, "PickupStaticMesh")
print(f"TSoftObjectPtr<UStaticMesh> format: {tsoft_object_ptr_format}")
tsoft_class_ptr_format = ides_subsystem.get_property_value_generic_text(item_definition, pickup_component_type, "PickupEffectOverride")
print(f"TSoftClassPtr<AFortPickupEffect> format: {tsoft_class_ptr_format}")
enum_format = ides_subsystem.get_property_value_generic_text(item_definition, pickup_component_type, "DropBehavior")
print(f"EWorldItemDropBehavior format: {enum_format}")

#OwnerPose
owner_pose_component_type = "/Script/ItemizationCoreRuntime.ItemComponentData_OwnerPose"
if ides_subsystem.add_component_data_entry(item_definition, owner_pose_component_type) is True:
    tobject_ptr_format = ides_subsystem.get_property_value_generic_text(item_definition, owner_pose_component_type, "EquippedPose")
    print(f"TObjectPtr<UItemOwnerPoseAsset> format: {tobject_ptr_format}")

#Traits Component
traits_component_type = "/Script/ItemizationCoreRuntime.ItemComponentData_Traits"
tag_to_add = ides_subsystem.find_existing_tag_by_name("Item.Trait.DisallowQuickbarFocus")
gameplay_tag_container = ides_subsystem.get_property_value_gameplay_tag_container(item_definition, traits_component_type, "Traits")
print(f"ItemComponentData_Traits Traits(FGameplayTagContainer) is: {gameplay_tag_container}")
if unreal.GameplayTagLibrary.has_tag(gameplay_tag_container, tag_to_add, True) is False:
    gameplay_tag_container.gameplay_tags.append(tag_to_add)
    print(f"ItemComponentData_Traits Traits(FGameplayTagContainer) Add {tag_to_add}: {gameplay_tag_container}")
else:
    gameplay_tag_container.gameplay_tags.remove(tag_to_add)
    print(f"ItemComponentData_Traits Traits(FGameplayTagContainer) Remove {tag_to_add}: {gameplay_tag_container}")
ides_subsystem.set_property_value_gameplay_tag_container(item_definition, traits_component_type, "Traits", gameplay_tag_container)

print("End test")

using System.Collections.Generic;
using UnityEngine;

namespace RobloxBridge
{
    /// The original Roblox values of whatever this object came from (a PointLight/SpotLight/SurfaceLight,
    /// the Lighting service, a Sky, Atmosphere, post effect...). Read-only record from the export;
    /// editing it doesn't change the Unity light. Attributes are listed with a leading "@".
    [AddComponentMenu("Roblox Bridge/Roblox Data")]
    public class RobloxData : MonoBehaviour
    {
        [System.Serializable]
        public class Entry
        {
            public string className;
            public string name;
            public string path;
            public List<Prop> props = new List<Prop>();
        }

        [System.Serializable]
        public class Prop
        {
            public string name;
            public string value;
        }

        public List<Entry> entries = new List<Entry>();

        public string Get(string prop, string className = null)
        {
            foreach (var e in entries)
                if (className == null || e.className == className)
                    foreach (var p in e.props)
                        if (p.name == prop) return p.value;
            return null;
        }
    }
}

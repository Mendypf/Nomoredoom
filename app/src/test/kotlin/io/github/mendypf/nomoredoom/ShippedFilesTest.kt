package io.github.mendypf.nomoredoom

import io.github.mendypf.nomoredoom.core.Gags
import io.github.mendypf.nomoredoom.core.Rulebook
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * Checks on the files that ship inside the app, the ones a typo in would only
 * show up on a phone: the rules file, the memes, the list of watched apps, the manifest.
 */
class ShippedFilesTest {
    private val android = "http://schemas.android.com/apk/res/android"
    private val main = File("src/main")

    private fun xml(path: String) = DocumentBuilderFactory.newInstance()
        .apply { isNamespaceAware = true }
        .newDocumentBuilder()
        .parse(File(main, path))

    private val watchedPackages: Set<String> =
        xml("res/xml/watch_service.xml").documentElement
            .getAttributeNS(android, "packageNames")
            .split(',').map { it.trim() }.toSet()

    @Test
    fun `the built-in rules file reads cleanly against the apps the service listens to`() {
        val book = Rulebook.parse(File(main, "res/raw/rules.txt").readText(), watchedPackages)
        assertEquals(watchedPackages, book.rules.map { it.pkg }.toSet(), "every watched app needs a rule, and only those")
    }

    @Test
    fun `the built-in memes read cleanly, 27 of them, each once`() {
        val gags = Gags.parse(File(main, "res/raw/gags.txt").readText())
        assertEquals(27, gags.media.size)
    }

    @Test
    fun `the service listens to exactly the short-video apps`() {
        assertEquals(
            setOf(
                "com.instagram.android",
                "com.google.android.youtube",
                "com.zhiliaoapp.musically",
                "com.ss.android.ugc.trill",
            ),
            watchedPackages,
        )
    }

    @Test
    fun `the service only asks for screen-change events`() {
        val types = xml("res/xml/watch_service.xml").documentElement
            .getAttributeNS(android, "accessibilityEventTypes").split('|').toSet()
        assertEquals(setOf("typeWindowStateChanged", "typeWindowContentChanged"), types)
    }

    @Test
    fun `the manifest asks for internet and nothing else`() {
        val manifest = xml("AndroidManifest.xml")
        val asked = manifest.getElementsByTagName("uses-permission")
        assertEquals(1, asked.length, "one <uses-permission> only")
        assertEquals("android.permission.INTERNET", asked.item(0).attributes.getNamedItemNS(android, "name").nodeValue)
        for (tag in listOf("uses-permission-sdk-23", "permission")) {
            assertEquals(0, manifest.getElementsByTagName(tag).length, "<$tag> found in the manifest")
        }
        val app = manifest.getElementsByTagName("application").item(0)
        assertEquals("false", app.attributes.getNamedItemNS(android, "usesCleartextTraffic").nodeValue, "https only")
    }

    @Test
    fun `nothing but the system can bind the service and nothing but adb can send it settings`() {
        val manifest = xml("AndroidManifest.xml")
        val service = manifest.getElementsByTagName("service").item(0)
        val receiver = manifest.getElementsByTagName("receiver").item(0)
        assertEquals(
            "android.permission.BIND_ACCESSIBILITY_SERVICE",
            service.attributes.getNamedItemNS(android, "permission").nodeValue,
        )
        assertEquals("android.permission.DUMP", receiver.attributes.getNamedItemNS(android, "permission").nodeValue)
        assertTrue(manifest.getElementsByTagName("provider").length == 0)
    }
}

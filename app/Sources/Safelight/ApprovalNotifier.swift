import Foundation
import SafelightCore
import UserNotifications

/// Shows a macOS notification for each change waiting for the user's approval, with
/// Approve and Decline buttons. The popover shows the same requests.
@MainActor
final class ApprovalNotifier: NSObject, UNUserNotificationCenterDelegate {
    static let category = "SAFELIGHT_APPROVAL"
    static let approve = "APPROVE"
    static let decline = "DECLINE"

    /// Called with (approval id, approve) when the user answers in a notification.
    var onAnswer: ((String, Bool) -> Void)?
    private var shown: Set<String> = []
    private let center = UNUserNotificationCenter.current()

    override init() {
        super.init()
        center.delegate = self
        let approve = UNNotificationAction(identifier: Self.approve, title: "Approve", options: [.authenticationRequired])
        let decline = UNNotificationAction(identifier: Self.decline, title: "Decline", options: [.destructive])
        center.setNotificationCategories([
            UNNotificationCategory(identifier: Self.category, actions: [approve, decline], intentIdentifiers: []),
        ])
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    /// Posts a notification for new requests and removes those no longer waiting.
    func update(_ approvals: [HubStatus.Approval]) {
        let waiting = Set(approvals.map(\.id))
        let gone = shown.subtracting(waiting)
        if !gone.isEmpty {
            center.removeDeliveredNotifications(withIdentifiers: Array(gone))
            shown.subtract(gone)
        }
        for approval in approvals where !shown.contains(approval.id) {
            shown.insert(approval.id)
            let content = UNMutableNotificationContent()
            content.title = "Approve this change?"
            let client = ActivityText.clientName(approval.client) ?? "An AI client"
            content.body = "\(client): \(approval.title). \(approval.reason)"
            content.categoryIdentifier = Self.category
            content.sound = .default
            center.add(UNNotificationRequest(identifier: approval.id, content: content, trigger: nil))
        }
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        let id = response.notification.request.identifier
        let action = response.actionIdentifier
        Task { @MainActor in
            if action == Self.approve {
                self.onAnswer?(id, true)
            } else if action == Self.decline {
                self.onAnswer?(id, false)
            }
            // A click on the notification itself opens nothing: the popover has the request.
        }
        completionHandler()
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound])
    }
}
